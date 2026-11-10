"""Run pre-approved local jobs inside slm-physics. No shell, no network jobs.

Allowlist (all executed with the slm-physics .venv python, cwd=repo):
- pytest: `python -m pytest -q [tests/test_*.py]`
- eval_oracle: `python eval_toolcalls.py data/toolcalls/test.jsonl --oracle`

`run_eval.py` is deliberately NOT offered: it calls NVIDIA's API over the
network, which conflicts with the local-only rule. `train_sft.py` and full
`predict.py` runs are also excluded (GPU/heavy). Ask explicitly if needed.

`write_project_doc` stages a local-LLM markdown draft (.md, repo root only)
and writes the file only after the user's explicit yes with a matching
stage_id. It never overwrites an existing file.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

logger = logging.getLogger("merlin.tools.project_runner")

_DEFAULT_SLM = Path(r"C:\Users\lindo\Documents\Workspace\IA\slm-physics")
try:
    _TIMEOUT_S = int(os.environ.get("MERLIN_PROJECT_JOB_TIMEOUT", "300"))
except (ValueError, TypeError):
    _TIMEOUT_S = 300
_MAX_OUT = 6000


def repo_path() -> Path:
    override = (os.environ.get("MERLIN_SLM_PHYSICS_PATH") or "").strip()
    return Path(override).expanduser() if override else _DEFAULT_SLM


def _venv_python(root: Path) -> str:
    if os.name == "nt":
        cand = root / ".venv" / "Scripts" / "python.exe"
    else:
        cand = root / ".venv" / "bin" / "python"
    return str(cand) if cand.exists() else sys.executable


def _run(argv: list[str], root: Path, timeout_s: int) -> dict:
    try:
        proc = subprocess.run(argv, cwd=str(root), shell=False,
                              capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timed out after {timeout_s}s."}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    out = out.strip()
    truncated = len(out) > _MAX_OUT
    return {"ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": out[:_MAX_OUT] + ("..." if truncated else ""),
            "counts": _extract_counts(out),
            "error": "" if proc.returncode == 0 else out[:2000]}


def _extract_counts(output: str) -> str:
    """Pull the verifiable result lines out of pytest/eval output.

    The explanation prompt must quote these verbatim instead of
    reconstructing numbers from prose.
    """
    import re
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    pytest_lines = [line for line in lines
                    if re.search(r"\d+\s+(passed|failed|error)", line)]
    if pytest_lines:
        return pytest_lines[-1][:200]
    metric_lines = [line for line in lines
                    if re.search(r"(EXACT MATCH|False-call|Missed-call|"
                                 r"Tool accuracy|Exact match)\s*:",
                                 line, re.IGNORECASE)]
    if metric_lines:
        return " | ".join(metric_lines[:6])[:500]
    return ""


def run_pytest(target: str = "") -> dict:
    """Run the local pytest suite (or one test file). Fully offline."""
    root = repo_path()
    if not (root / "tests").is_dir():
        return {"ok": False, "error": f"slm-physics not found at {root}"}
    args = ["-m", "pytest", "-q"]
    cleaned = (target or "").strip().replace("\\", "/")
    if cleaned:
        # Only files inside tests/ ending in .py.
        if not cleaned.startswith("tests/") or not cleaned.endswith(".py"):
            return {"ok": False, "error": "Only tests/test_*.py allowed."}
        candidate = (root / cleaned).resolve()
        try:
            if candidate != (root / "tests").resolve() and \
                    (root / "tests").resolve() not in candidate.parents:
                raise ValueError
        except ValueError:
            return {"ok": False, "error": "Test path is outside tests/; refused."}
        if not candidate.is_file():
            return {"ok": False, "error": f"Test not found: {cleaned}"}
        args.append(cleaned)
    result = _run([_venv_python(root), *args], root, _TIMEOUT_S)
    result["job"] = "pytest"
    return result


def run_eval_oracle() -> dict:
    """Score gold tool-calls against themselves (local sanity check)."""
    root = repo_path()
    dataset = root / "data" / "toolcalls" / "test.jsonl"
    script = root / "eval_toolcalls.py"
    if not dataset.is_file():
        return {"ok": False,
                "error": "data/toolcalls/test.jsonl missing; run make_dataset.py first."}
    if not script.is_file():
        return {"ok": False, "error": "eval_toolcalls.py not found."}
    result = _run([_venv_python(root), "eval_toolcalls.py",
                   "data/toolcalls/test.jsonl", "--oracle"], root, _TIMEOUT_S)
    result["job"] = "eval_oracle"
    return result


def run_project_job(job: str = "", target: str = "") -> dict:
    """Dispatch one allowlisted local job. Everything else fails closed."""
    name = (job or "").strip().lower()
    if name in ("pytest", "tests", "test"):
        return run_pytest(target)
    if name in ("eval_oracle", "oracle", "toolcall_oracle"):
        if (target or "").strip():
            return {"ok": False,
                    "error": "eval_oracle takes no target; refused."}
        return run_eval_oracle()
    return {"ok": False,
            "error": f"Job {job!r} not allowlisted. Local jobs: pytest, eval_oracle. "
                     "run_eval.py needs NVIDIA's network API and train_sft.py needs "
                     "a GPU machine, so they are excluded by the local-only rule."}


_STAGED_DOCS: dict[str, dict] = {}
_DOC_BANNED = ("medicina", "laboratorio", "hospital", "clínica", "clinica")


def _repo_facts() -> tuple[str, str, str]:
    """Read-only repo context to ground the doc draft (summary, tools, README)."""
    summary, tools, readme = "", "", ""
    try:
        from merlin.tools import physics_bridge
        info = physics_bridge.analyze_repo("", "summary")
        if info.get("ok"):
            summary = str(info.get("summary", ""))
            tools = ", ".join(info.get("tools", []) or [])
    except Exception:
        logger.debug("repo facts unavailable", exc_info=True)
    try:
        text = (repo_path() / "README.md").read_text(
            encoding="utf-8", errors="replace")
        readme = "\n".join(text.splitlines()[:60])[:2500]
    except Exception:
        pass
    return summary, tools, readme


def _draft_doc(topic: str, filename: str, language: str) -> str:
    """Draft the markdown with the local fast model, grounded in repo facts.

    The main model is too slow on CPU-only machines for an interactive
    draft; the fast model suffices because the user reviews the draft
    before anything is written, and the banned-topic guard rejects
    off-topic output.
    """
    from merlin.llm import get_llm
    summary, tools, readme = _repo_facts()
    es = str(language).lower().startswith("es")
    lang = "Spanish" if es else "English"
    base_prompt = (
        "You are MERLIN's science assistant writing a project note for "
        "slm-physics. Hard facts, never violate them: SLM means Small "
        "Language Model for physics (a local physics verification assistant "
        "for quantum claims: hermiticity, unitarity, commutators, density "
        "matrices, unitary evolution, uncertainty relations, dimensional "
        "analysis). It has NOTHING to do with medicine, laboratories, "
        "hospitals or clinics. Every improvement you propose must be a "
        "concrete software/physics task for THIS repo.\n\n"
        f"Repo facts: {summary}\n"
        f"Available verification checks: {tools}\n"
        f"README (head):\n{readme}\n\n"
        f"Write in {lang} a Markdown document about: {topic or 'improvements and next steps'}. "
        "Structure: title with the file topic, short intro (what slm-physics "
        "is, one paragraph), numbered improvements (each with why + how), "
        "and a short priority order. Max 60 lines. Return ONLY the Markdown, "
        "no preamble.")
    for attempt in ("", "CORRECTION: the previous draft mentioned medicine. "
                          "Rewrite with ZERO medical content. "):
        answer = get_llm("fast").chat(
            [{"role": "user", "content": attempt + base_prompt}],
            fallback="")
        draft = re.sub(r"\s+$", "", str(answer or "")).strip()
        if draft and not any(b in draft.lower() for b in _DOC_BANNED):
            return draft
        logger.warning("project doc draft rejected (empty or off-topic)")
    return ""


def write_project_doc(path: str = "", topic: str = "", stage_id: str = "",
                      publish: bool = False, language: str = "es") -> dict:
    """Stage a .md draft for slm-physics, or write it after confirmation."""
    root = repo_path()
    name = (path or "").strip().replace("\\", "/").strip("/ ") or "MEJORAS.md"
    if ("/" in name or not name.lower().endswith(".md")
            or not re.fullmatch(r"[A-Za-z0-9_][\w\-.]*\.md", name)):
        return {"ok": False,
                "error": "Solo archivos .md en la raíz de slm-physics "
                         "(ej. MEJORAS.md); sin subcarpetas."}
    if publish:
        staged = _STAGED_DOCS.get(name)
        if not staged or staged.get("id") != (stage_id or ""):
            return {"ok": False,
                    "error": "El borrador venció o no coincide; vuelve a pedirlo."}
        target = root / name
        if target.exists():
            return {"ok": False,
                    "error": f"{name} ya existe; no lo sobrescribo."}
        try:
            target.write_text(staged["draft"], encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        _STAGED_DOCS.pop(name, None)
        return {"ok": True, "published": True, "path": name}
    if not (root / "physics_tools").is_dir():
        return {"ok": False,
                "error": f"slm-physics no encontrado en {root}."}
    if (root / name).exists():
        return {"ok": False,
                "error": f"{name} ya existe; pide otro nombre o bórralo tú."}
    draft = _draft_doc(topic, name, language)
    if not draft:
        return {"ok": False,
                "error": "No pude redactar un borrador válido con el modelo "
                         "local; inténtalo de nuevo."}
    token = uuid.uuid4().hex
    _STAGED_DOCS[name] = {"id": token, "draft": draft}
    return {"ok": True, "staged": True, "needs_confirmation": True,
            "stage_id": token, "path": name, "draft": draft}
