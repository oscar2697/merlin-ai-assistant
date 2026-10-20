"""Regression checks for safe OS tool dispatch and shell invocation."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.agents.os_agent import _run_step
from merlin.tools import system_tools


def test_unknown_tool_fails_closed_without_keyerror():
    name, args, result = _run_step({"tool": "not_a_real_tool", "command": "bad"})
    assert name == "none"
    assert args == {}
    assert result["ok"] is False
    assert "Unknown OS tool" in result["error"]


def test_invalid_plan_fails_closed():
    assert _run_step(None)[2]["ok"] is False


def test_terminal_uses_explicit_shell_without_shell_true():
    completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
    with patch.object(system_tools, "get_config", return_value=SimpleNamespace(
            command_blacklist=(), terminal_timeout_seconds=3)), \
            patch.object(system_tools.subprocess, "run", return_value=completed) as run:
        result = system_tools.execute_terminal_command("echo ok")
    assert result["ok"] is True
    assert run.call_args.args[0][-2:] in (["-c", "echo ok"], ["/c", "echo ok"])
    assert run.call_args.kwargs["shell"] is False


if __name__ == "__main__":
    test_unknown_tool_fails_closed_without_keyerror()
    test_invalid_plan_fails_closed()
    test_terminal_uses_explicit_shell_without_shell_true()
    print("PASS: invalid OS plans fail closed and terminal avoids shell=True")
