"""OpenCode orchestration skills: prompt injection + status reading."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from merlin.config import get_config

logger = logging.getLogger("merlin.tools.opencode")


def inject_opencode_prompt(project_path: str, prompt_content: str, launch: bool = True) -> dict:
    """Write PROMPT_OPENCODE.md into the project and (optionally) launch OpenCode."""
    try:
        root = Path(project_path).expanduser() if project_path else Path.cwd()
        root.mkdir(parents=True, exist_ok=True)
        target = root / get_config().opencode_prompt_filename
        target.write_text(prompt_content, encoding="utf-8")

        result = {"ok": True, "prompt_file": str(target), "launched": False}
        if launch:
            if shutil.which("opencode"):
                subprocess.Popen(
                    ["opencode"], cwd=str(root),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                result["launched"] = True
            elif shutil.which("code"):
                subprocess.Popen(["code", str(root)])
                result["launched"] = True
                result["note"] = "OpenCode CLI not found; opened VS Code instead."
            else:
                result["note"] = "No coding editor CLI found; prompt file written only."
        return result
    except Exception as exc:
        logger.exception("inject_opencode_prompt failed")
        return {"ok": False, "error": str(exc)}


def read_opencode_status(file_path: str) -> dict:
    """Read execution output / generated files from an OpenCode session."""
    try:
        path = Path(file_path).expanduser()
        if not path.exists():
            return {"ok": False, "error": f"File not found: {file_path}"}
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "file": str(path), "content": text[-3000:]}
    except Exception as exc:
        logger.exception("read_opencode_status failed")
        return {"ok": False, "error": str(exc)}
