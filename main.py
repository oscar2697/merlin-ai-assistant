"""MERLIN entry point.

Usage:
  python main.py                -> GUI mode (default)
  python main.py --cli          -> terminal chat mode
  python main.py --check        -> Ollama connectivity/model check
  python main.py --install-autostart  -> register Windows autostart tasks
"""
from __future__ import annotations

import argparse
import logging
import sys

from merlin.config import ensure_dirs, get_config
from merlin.events import drain_events, next_event
from merlin.llm import get_llm
from merlin.runtime import MerlinRuntime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("merlin.main")


def run_check() -> int:
    llm = get_llm()
    model = llm.detect_model()
    if model:
        print(f"Ollama OK - modelo disponible: {model}")
        return 0
    print("Ollama no está disponible o el modelo configurado no está instalado. "
          f"Comprueba el servidor en {get_config().lm_studio_base_url} y "
          f"que exista el modelo '{get_config().model_name}' (ollama list).")
    return 1


def install_autostart() -> int:
    if sys.platform != "win32":
        print("--install-autostart is Windows-only for now (macOS: launchd, future).")
        return 1
    import subprocess
    from pathlib import Path

    script = Path(__file__).parent / "installer" / "windows" / "setup_autostart.ps1"
    if not script.exists():
        print(f"Installer script not found: {script}")
        return 1
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        return 1
    print("Autostart tasks registered (MERLIN at logon).")
    return 0


def run_cli() -> None:
    runtime = MerlinRuntime()
    print("MERLIN CLI mode. Type your request ('exit' to quit).")
    while True:
        try:
            query = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query:
            continue
        if query.lower() in ("exit", "quit", "salir"):
            break
        runtime.submit_sync(query)
        for event in iter(lambda: next_event(timeout=0.2), None):
            if event.type == "merlin_response":
                print(f"\nMERLIN> {event.data}")
    print("Goodbye.")


def run_gui() -> None:
    from merlin.gui import run_gui as launch
    from merlin.greeting import greet_async
    runtime = MerlinRuntime()
    greet_async()  # formal, time-aware greeting (chat + voice)
    launch(runtime)


def main() -> int:
    parser = argparse.ArgumentParser(description="MERLIN local AI assistant")
    parser.add_argument("--cli", action="store_true", help="terminal chat mode")
    parser.add_argument("--check", action="store_true",
                        help="verify LM Studio connectivity and exit")
    parser.add_argument("--install-autostart", action="store_true",
                        help="register autostart tasks (Windows)")
    args = parser.parse_args()

    ensure_dirs()

    if args.check:
        return run_check()
    if args.install_autostart:
        return install_autostart()

    if not get_llm().health_check():
        logger.warning("Starting in DEGRADED MODE: Ollama/model is not reachable. "
                       "Deterministic fallbacks are active.")

    try:
        if args.cli:
            run_cli()
        else:
            run_gui()
    except ImportError as exc:
        logger.error("Missing dependency for this mode: %s "
                     "(pip install -r requirements.txt)", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())