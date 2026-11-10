"""Test plan normalization and _run_step argument passing for project & OS tools."""
import functools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from merlin.agents import os_agent


def _run_with_spy(raw_json: str):
    """Pass a JSON plan through _normalize_plan and _run_step with a tool call spy."""
    parsed = json.loads(raw_json)
    normalized = os_agent._normalize_plan(parsed)
    tool_name = normalized.get("tool")

    captured_kwargs = {}
    original_tool = os_agent._OS_TOOLS.get(tool_name)

    @functools.wraps(original_tool)
    def _spy_tool(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {"ok": True, "captured": kwargs}

    os_agent._OS_TOOLS[tool_name] = _spy_tool
    try:
        dispatched_tool, args, result = os_agent._run_step(normalized)
    finally:
        if original_tool is not None:
            os_agent._OS_TOOLS[tool_name] = original_tool

    return dispatched_tool, args, captured_kwargs, result


def test_check_physics_string_arguments():
    plan_json = json.dumps({
        "tool": "check_physics",
        "tool_name": "check_hermitian",
        "arguments": '{"matrix": [[1, 0], [0, 1]]}',
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "check_physics", tool_name
    assert args == {
        "tool_name": "check_hermitian",
        "arguments": '{"matrix": [[1, 0], [0, 1]]}',
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_check_physics_dict_arguments_not_flattened():
    plan_json = json.dumps({
        "tool": "check_physics",
        "tool_name": "check_unitary",
        "arguments": {"matrix": [[0, 1], [1, 0]]},
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "check_physics", tool_name
    assert args == {
        "tool_name": "check_unitary",
        "arguments": {"matrix": [[0, 1], [1, 0]]},
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_check_physics_args_alias():
    plan_json = json.dumps({
        "tool": "check_physics",
        "check": "check_normalized",
        "args": '{"state": [1, 0]}',
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "check_physics", tool_name
    assert args == {
        "tool_name": "check_normalized",
        "arguments": '{"state": [1, 0]}',
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_analyze_repo_path_preserved():
    plan_json = json.dumps({
        "tool": "analyze_repo",
        "path": "physics_tools",
        "scope": "summary",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "analyze_repo", tool_name
    assert args == {
        "path": "physics_tools",
        "scope": "summary",
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_analyze_repo_dir_alias():
    plan_json = json.dumps({
        "tool": "analyze_repo",
        "dir": "physics_tools/registry.py",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "analyze_repo", tool_name
    assert args == {
        "path": "physics_tools/registry.py",
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_run_project_job_target_preserved():
    plan_json = json.dumps({
        "tool": "run_project_job",
        "job": "pytest",
        "target": "tests/test_tools.py",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "run_project_job", tool_name
    assert args == {
        "job": "pytest",
        "target": "tests/test_tools.py",
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_run_project_job_aliases():
    plan_json = json.dumps({
        "tool": "run_project_job",
        "action": "pytest",
        "path": "tests/test_tools.py",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "run_project_job", tool_name
    assert args == {
        "job": "pytest",
        "target": "tests/test_tools.py",
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_write_project_doc_path_and_topic_preserved():
    plan_json = json.dumps({
        "tool": "write_project_doc",
        "path": "MEJORAS.md",
        "topic": "quantum tests and verification",
        "stage_id": "stage-42",
        "publish": False,
        "language": "es",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "write_project_doc", tool_name
    assert args == {
        "path": "MEJORAS.md",
        "topic": "quantum tests and verification",
        "stage_id": "stage-42",
        "publish": False,
        "language": "es",
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_write_project_doc_aliases():
    plan_json = json.dumps({
        "tool": "write_project_doc",
        "file": "MEJORAS.md",
        "instruction": "nuevas mejoras",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "write_project_doc", tool_name
    assert args == {
        "path": "MEJORAS.md",
        "topic": "nuevas mejoras",
    }, args
    assert captured == args, captured
    assert result["ok"] is True


def test_manage_files_path_alias_still_works():
    plan_json = json.dumps({
        "tool": "manage_files",
        "action": "list",
        "path": "C:/tmp",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "manage_files", tool_name
    assert args == {
        "action": "list",
        "source": "C:/tmp",
    }, args
    assert captured == args, captured


def test_write_text_topic_alias_still_works():
    plan_json = json.dumps({
        "tool": "write_text",
        "app": "Word",
        "topic": "write about science",
    })
    tool_name, args, captured, result = _run_with_spy(plan_json)
    assert tool_name == "write_text", tool_name
    assert args == {
        "app_name": "Word",
        "instruction": "write about science",
    }, args
    assert captured == args, captured


if __name__ == "__main__":
    test_check_physics_string_arguments()
    test_check_physics_dict_arguments_not_flattened()
    test_check_physics_args_alias()
    test_analyze_repo_path_preserved()
    test_analyze_repo_dir_alias()
    test_run_project_job_target_preserved()
    test_run_project_job_aliases()
    test_write_project_doc_path_and_topic_preserved()
    test_write_project_doc_aliases()
    test_manage_files_path_alias_still_works()
    test_write_text_topic_alias_still_works()
    print("PASS: plan normalization and _run_step argument routing verified")
