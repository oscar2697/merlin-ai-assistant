"""Offline checks for the allowlisted slm-physics job runner."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from merlin.agents import os_agent
from merlin.tools import project_runner


def test_unknown_job_fails_closed():
    result = project_runner.run_project_job("train_sft", "")
    assert result["ok"] is False, result
    assert "local-only" in result["error"], result


def test_run_eval_is_not_offered():
    result = project_runner.run_project_job("run_eval", "")
    assert result["ok"] is False, result


def test_pytest_target_escape_fails_closed():
    result = project_runner.run_project_job("pytest", r"C:\Windows\x.py")
    assert result["ok"] is False, result
    result = project_runner.run_project_job("pytest", "agent.py")
    assert result["ok"] is False, result


def test_eval_oracle_takes_no_target():
    result = project_runner.run_project_job("eval_oracle", "tests/test_tools.py")
    assert result["ok"] is False, result


def test_pytest_single_file_passes_locally():
    result = project_runner.run_project_job("pytest", "tests/test_tools.py")
    assert result["ok"] is True, result
    assert "passed" in (result.get("output") or "").lower(), result
    assert "passed" in (result.get("counts") or ""), result


def test_pytest_full_suite_counts_are_verbatim():
    result = project_runner.run_project_job("pytest", "")
    assert result["ok"] is True, result
    assert "passed" in (result.get("counts") or ""), result
    assert "failed" not in (result.get("counts") or "").lower(), result


def _stub_llm(draft):
    class _Stub:
        def chat(self, messages, fallback="", **kwargs):
            assert messages and "science assistant" in messages[0]["content"], messages
            return draft
    return _Stub()


def _temp_repo():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "physics_tools").mkdir()
    return tmp


def _swap_env_llm(repo, draft):
    """Point the runner at a temp repo with a stub LLM. Returns a restore fn."""
    import merlin.llm as llm_mod
    prev_repo = os.environ.get("MERLIN_SLM_PHYSICS_PATH")
    os.environ["MERLIN_SLM_PHYSICS_PATH"] = str(repo)
    real = llm_mod.get_llm
    llm_mod.get_llm = lambda *a, **k: _stub_llm(draft)

    def _restore():
        llm_mod.get_llm = real
        if prev_repo is None:
            os.environ.pop("MERLIN_SLM_PHYSICS_PATH", None)
        else:
            os.environ["MERLIN_SLM_PHYSICS_PATH"] = prev_repo
    return _restore


def test_doc_stage_and_publish_in_temp_repo():
    repo = _temp_repo()
    restore = _swap_env_llm(
        repo, "# Mejoras\n\nSLM es un verificador local de física.")
    try:
        staged = project_runner.write_project_doc(
            path="MEJORAS.md", topic="mejoras", language="es")
        assert staged.get("staged") and staged.get("stage_id"), staged
        assert "física" in staged["draft"], staged
        published = project_runner.write_project_doc(
            path="MEJORAS.md", stage_id=staged["stage_id"], publish=True)
        assert published.get("published"), published
        assert (repo / "MEJORAS.md").read_text(
            encoding="utf-8").startswith("# Mejoras")
    finally:
        restore()


def test_doc_medical_draft_is_rejected():
    repo = _temp_repo()
    restore = _swap_env_llm(
        repo, "# Doc\n\nSistema de Laboratorio de Medicina.")
    try:
        result = project_runner.write_project_doc(
            path="DOC.md", topic="mejoras", language="es")
        assert result["ok"] is False, result
        assert not (repo / "DOC.md").exists()
    finally:
        restore()


def test_doc_publish_guards():
    repo = _temp_repo()
    prev = os.environ.get("MERLIN_SLM_PHYSICS_PATH")
    os.environ["MERLIN_SLM_PHYSICS_PATH"] = str(repo)
    try:
        assert project_runner.write_project_doc(
            path="X.md", stage_id="nope", publish=True)["ok"] is False
        assert project_runner.write_project_doc(
            path="../X.md", topic="t")["ok"] is False
        assert project_runner.write_project_doc(
            path="nota.txt", topic="t")["ok"] is False
        assert project_runner.write_project_doc(
            path="sub/X.md", topic="t")["ok"] is False
        (repo / "YA.md").write_text("x", encoding="utf-8")
        assert project_runner.write_project_doc(
            path="YA.md", topic="t")["ok"] is False
    finally:
        if prev is None:
            os.environ.pop("MERLIN_SLM_PHYSICS_PATH", None)
        else:
            os.environ["MERLIN_SLM_PHYSICS_PATH"] = prev


def test_doc_plan_routes_before_word():
    plan = os_agent._plan_with_keywords(
        "crea el archivo MEJORAS.md en el proyecto slm sobre mejoras")
    assert plan and plan["tool"] == "write_project_doc", plan
    assert plan["path"] == "MEJORAS.md", plan
    assert "mejoras" in plan["topic"].lower(), plan


def test_explanation_gate_needs_substance():
    assert os_agent._should_explain_tool_result(
        "run_project_job", {"ok": True}) is True
    assert os_agent._should_explain_tool_result(
        "run_project_job", {"ok": False, "output": "FAILED x"}) is True
    assert os_agent._should_explain_tool_result(
        "run_project_job", {"ok": False, "output": ""}) is False
    assert os_agent._should_explain_tool_result(
        "write_project_doc", {"ok": False, "error": "x"}) is False
    assert os_agent._should_explain_tool_result(
        "analyze_repo", {"ok": False, "error": "x"}) is False


def test_project_tools_plan_normalization_and_run_step():
    """Verify that JSON plans for SLM/project tools reach the tool without losing args."""
    import functools
    import json

    cases = [
        (
            '{"tool": "check_physics", "tool_name": "check_hermitian", "arguments": "{\\"matrix\\": [[1, 0], [0, 1]]}"}',
            "check_physics",
            {"tool_name": "check_hermitian", "arguments": '{"matrix": [[1, 0], [0, 1]]}'},
        ),
        (
            '{"tool": "check_physics", "tool_name": "check_unitary", "arguments": {"matrix": [[0, 1], [1, 0]]}}',
            "check_physics",
            {"tool_name": "check_unitary", "arguments": {"matrix": [[0, 1], [1, 0]]}},
        ),
        (
            '{"tool": "analyze_repo", "path": "physics_tools", "scope": "summary"}',
            "analyze_repo",
            {"path": "physics_tools", "scope": "summary"},
        ),
        (
            '{"tool": "run_project_job", "job": "pytest", "target": "tests/test_tools.py"}',
            "run_project_job",
            {"job": "pytest", "target": "tests/test_tools.py"},
        ),
        (
            '{"tool": "write_project_doc", "path": "MEJORAS.md", "topic": "mejoras", "stage_id": "s1", "publish": false, "language": "es"}',
            "write_project_doc",
            {"path": "MEJORAS.md", "topic": "mejoras", "stage_id": "s1", "publish": False, "language": "es"},
        ),
    ]

    for raw_json, expected_tool, expected_args in cases:
        normalized = os_agent._normalize_plan(json.loads(raw_json))
        tool_name = normalized.get("tool")
        assert tool_name == expected_tool, (tool_name, expected_tool)

        captured = {}
        original = os_agent._OS_TOOLS[tool_name]

        @functools.wraps(original)
        def _spy(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        os_agent._OS_TOOLS[tool_name] = _spy
        try:
            dispatched, args, res = os_agent._run_step(normalized)
        finally:
            os_agent._OS_TOOLS[tool_name] = original

        assert dispatched == expected_tool, dispatched
        assert args == expected_args, (args, expected_args)
        assert captured == expected_args, (captured, expected_args)
        assert res.get("ok") is True, res


if __name__ == "__main__":
    test_unknown_job_fails_closed()
    test_run_eval_is_not_offered()
    test_pytest_target_escape_fails_closed()
    test_eval_oracle_takes_no_target()
    test_pytest_single_file_passes_locally()
    test_pytest_full_suite_counts_are_verbatim()
    test_doc_stage_and_publish_in_temp_repo()
    test_doc_medical_draft_is_rejected()
    test_doc_publish_guards()
    test_doc_plan_routes_before_word()
    test_explanation_gate_needs_substance()
    test_project_tools_plan_normalization_and_run_step()
    print("PASS: project runner stays on the local allowlist")
