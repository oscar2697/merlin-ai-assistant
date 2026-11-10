"""Offline checks for the read-only slm-physics bridge."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from merlin.agents import os_agent
from merlin.tools import physics_bridge


def test_physics_plan_routes_to_bridge():
    plan = os_agent._plan_with_keywords("revisa mi slm physics")
    assert plan and plan["tool"] == "analyze_repo", plan
    plan = os_agent._plan_with_keywords(
        "check_hermitian [[1, 0], [0, 1]] quantum")
    assert plan and plan["tool"] == "check_physics", plan
    plan = os_agent._plan_with_keywords("corre los tests de mi slm physics")
    assert plan and plan["tool"] == "run_project_job", plan
    assert plan["job"] == "pytest", plan
    plan = os_agent._plan_with_keywords("corre el eval oracle de slm physics")
    assert plan and plan["tool"] == "run_project_job", plan
    assert plan["job"] == "eval_oracle", plan


def test_word_writing_summary_not_routed_to_analyze_repo():
    query = "escribe un resumen sobre física cuántica en Word"
    physics_plan = os_agent._physics_plan(query)
    assert physics_plan is None or physics_plan.get("tool") != "analyze_repo", physics_plan
    plan = os_agent._plan_with_keywords(query)
    assert plan and plan.get("tool") != "analyze_repo", plan
    assert plan.get("tool") == "write_text", plan
    assert plan.get("app_name") == "Word", plan


def test_general_physics_questions_not_routed_to_os():
    from merlin import router
    for query in (
        "qué es la física cuántica",
        "explícame qué es quantum mechanics",
        "qué es un markdown",
        "qué mejoras tiene Python 3.12",
    ):
        route = router.route_query(query)
        assert route != router.ROUTE_OS, (query, route)


def test_slm_project_intent_routes_to_os():
    from merlin import router
    for query in (
        "revisa mi slm physics",
        "check_hermitian [[1, 0], [0, 1]]",
        "crea MEJORAS.md en el proyecto slm",
        "corre los tests de slm physics",
        "analiza mi proyecto slm",
    ):
        route = router.route_query(query)
        assert route == router.ROUTE_OS, (query, route)


def test_unknown_physics_tool_fails_closed():
    result = physics_bridge.check_physics("delete_everything", "{}")
    assert result["ok"] is False, result


def test_analyze_repo_stays_inside_slm():
    result = physics_bridge.analyze_repo(r"C:\Windows", "summary")
    assert result["ok"] is False, result


def test_analyze_repo_summary_is_readonly():
    result = physics_bridge.analyze_repo("", "summary")
    assert result["ok"] is True, result
    assert "checks" in result["summary"].lower(), result
    assert "files" not in result, result  # file list only on detail scope


def test_hermitian_check_passes_locally():
    result = physics_bridge.check_physics(
        "check_hermitian", '{"matrix": [[1, 0], [0, 1]]}')
    assert result["ok"] is True and result["passed"] is True, result


def test_broken_physics_call_is_not_a_failed_check():
    result = physics_bridge.check_physics("check_hermitian", '{"matrix": "nope"}')
    assert result["ok"] is False, result


def test_explanation_prompt_is_pure_and_bilingual():
    for lang, tool, result in (
            ("es", "analyze_repo",
             {"summary": "slm-physics: verificador", "tools": ["check_hermitian"],
              "tests": 5, "eval_cases": 9, "eval_real": 42}),
            ("en", "run_project_job",
             {"job": "pytest", "ok": False, "output": "FAILED x", "error": ""}),
            ("es", "check_physics",
             {"tool": "check_hermitian", "passed": True, "summary": "ok"})):
        messages = os_agent._project_explanation_messages(tool, result, lang, "q")
        assert len(messages) == 2, messages
        assert messages[0]["role"] == "system", messages
        assert ("Spanish" if lang == "es" else "English") in messages[0]["content"]
        assert messages[1]["role"] == "user", messages


def test_explanation_skips_intro_when_briefed():
    result = {"job": "pytest", "ok": False, "output": "FAILED x", "error": ""}
    first = os_agent._project_explanation_messages(
        "run_project_job", result, "es", "q", briefed=False)[0]["content"]
    second = os_agent._project_explanation_messages(
        "run_project_job", result, "es", "q", briefed=True)[0]["content"]
    assert "one line" in first, first
    assert "do NOT introduce" in second, second
    assert "exact job" in second, second


if __name__ == "__main__":
    test_physics_plan_routes_to_bridge()
    test_word_writing_summary_not_routed_to_analyze_repo()
    test_general_physics_questions_not_routed_to_os()
    test_slm_project_intent_routes_to_os()
    test_unknown_physics_tool_fails_closed()
    test_analyze_repo_stays_inside_slm()
    test_analyze_repo_summary_is_readonly()
    test_hermitian_check_passes_locally()
    test_broken_physics_call_is_not_a_failed_check()
    test_explanation_prompt_is_pure_and_bilingual()
    test_explanation_skips_intro_when_briefed()
    print("PASS: physics bridge stays local and read-only")
