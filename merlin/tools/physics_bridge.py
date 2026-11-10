"""Read-only bridge to the local slm-physics repo.

MERLIN never writes to slm-physics through this module: it only imports
`physics_tools.registry.call_tool` (pure NumPy/SciPy/pint checks) and lists
files for `analyze_repo`. No subprocess, no shell, no network.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("merlin.tools.physics")

_DEFAULT_SLM = Path(r"C:\Users\lindo\Documents\Workspace\IA\slm-physics")

_SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", ".pytest_cache",
              ".mypy_cache", "node_modules", ".idea", ".vscode"}


def slm_path() -> Path:
    override = (os.environ.get("MERLIN_SLM_PHYSICS_PATH") or "").strip()
    return Path(override).expanduser() if override else _DEFAULT_SLM


def _load_registry():
    """Import physics_tools.registry without permanently polluting sys.path."""
    root = slm_path()
    pkg = root / "physics_tools"
    if not pkg.is_dir():
        raise FileNotFoundError(f"slm-physics not found at {root}")
    added = False
    str_root = str(root)
    if str_root not in sys.path:
        sys.path.insert(0, str_root)
        added = True
    try:
        import importlib
        return importlib.import_module("physics_tools.registry")
    finally:
        if added:
            try:
                sys.path.remove(str_root)
            except ValueError:
                pass


def check_physics(tool_name: str = "", arguments: str | dict = "") -> dict:
    """Run one slm-physics check. Read-only, CPU-only, fully local."""
    name = (tool_name or "").strip()
    if not name:
        return {"ok": False, "error": "No physics tool named."}
    try:
        registry = _load_registry()
    except Exception as exc:
        return {"ok": False, "error": f"slm-physics unavailable: {exc}"}
    allowed = {t["function"]["name"] for t in getattr(registry, "TOOLS", [])}
    if name not in allowed:
        return {"ok": False, "error": f"Unknown physics tool: {name!r}"}
    try:
        raw = registry.call_tool(name, arguments if isinstance(arguments, (str, dict)) else "")
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        logger.exception("physics check failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(data, dict) and "error" in data and "passed" not in data:
        return {"ok": False, "error": str(data["error"])}
    if not isinstance(data, dict) or "passed" not in data:
        return {"ok": False, "error": "Physics tool returned an unexpected shape."}
    return {"ok": True, "tool": name,
            "passed": bool(data.get("passed")),
            "summary": str(data.get("summary", ""))[:2000],
            "details": data.get("details", {})}


def _read_head(path: Path, max_lines: int = 12, max_chars: int = 800) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[:max_lines])[:max_chars]


def _count_jsonl(path: Path) -> int | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return sum(1 for line in fh if line.strip())
    except Exception:
        return None


def analyze_repo(path: str = "", scope: str = "summary") -> dict:
    """Summarize the slm-physics tree. Never writes, never executes."""
    root = slm_path()
    target = (Path(path).expanduser() if (path or "").strip()
              else root)
    if not target.is_absolute():
        target = root / target
    try:
        target = target.resolve()
        root_resolved = root.resolve()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    # Fail closed: only paths inside the slm-physics repo.
    if target != root_resolved and root_resolved not in target.parents:
        return {"ok": False, "error": "Path is outside slm-physics; refused."}
    if not target.exists():
        return {"ok": False, "error": f"Path not found: {target}"}
    if target.is_file():
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        lines = text.splitlines()
        head = "\n".join(lines[:60])
        return {"ok": True, "path": str(target), "lines": len(lines),
                "head": head[:6000]}
    files: list[str] = []
    py_files: list[str] = []
    for p in sorted(target.rglob("*")):
        try:
            rel = p.relative_to(target)
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if p.is_dir():
            continue
        files.append(str(rel).replace("\\", "/"))
        if p.suffix == ".py":
            py_files.append(str(rel).replace("\\", "/"))
        if len(files) >= 400:
            break
    # Semantic summary (kept short: the results card shows `summary`, not files).
    try:
        registry = _load_registry()
        tool_names = [t["function"]["name"]
                      for t in getattr(registry, "TOOLS", [])]
    except Exception:
        tool_names = []
    n_tests = len(list((root_resolved / "tests").glob("test_*.py"))) \
        if (root_resolved / "tests").is_dir() else 0
    n_cases = _count_jsonl(root_resolved / "eval" / "cases_v0.jsonl")
    n_real = _count_jsonl(root_resolved / "eval" / "real_claims.jsonl")
    has_weights = any((root_resolved / name).exists()
                      for name in ("run1", "models", "checkpoints", "ckpt"))
    parts = [f"{root_resolved.name}: verificador local de física "
             f"({len(py_files)} py, {n_tests} tests)"]
    if tool_names:
        parts.append(f"{len(tool_names)} checks: " + ", ".join(tool_names))
    eval_bits = []
    if n_cases is not None:
        eval_bits.append(f"{n_cases} casos base")
    if n_real is not None:
        eval_bits.append(f"{n_real} reales")
    if eval_bits:
        parts.append("eval: " + " + ".join(eval_bits))
    parts.append("entradas: agent.py, run_eval.py, train_sft.py")
    parts.append("estado: en desarrollo, "
                 + ("con pesos" if has_weights else "sin pesos entrenados"))
    summary = ". ".join(parts) + "."
    out: dict = {"ok": True, "path": str(target), "summary": summary,
                 "tools": tool_names, "tests": n_tests,
                 "eval_cases": n_cases, "eval_real": n_real,
                 "total_files": len(files), "python_files": len(py_files)}
    if scope == "summary":
        return out
    out["files"] = sorted(files)[:120]
    return out
