"""OS & system control skills (psutil, terminal, apps, files, media, WhatsApp)."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

import httpx
import psutil

from merlin.config import get_config

logger = logging.getLogger("merlin.tools.system")


def check_command_safety(command: str) -> Tuple[bool, str]:
    """Enforce the minimal blacklist of irreversible actions."""
    c = (command or "").lower()
    for bad in get_config().command_blacklist:
        if bad in c:
            return False, f"Blocked: '{bad}' is on the irreversible-action blacklist."
    return True, ""


def control_system_resource(action: str) -> dict:
    """action: ram | cpu | battery | processes -> real-time hardware status."""
    action = (action or "").lower().strip()
    try:
        if action == "ram":
            vm = psutil.virtual_memory()
            return {"ok": True, "action": action, "data": {
                "total_gb": round(vm.total / 1e9, 1),
                "used_gb": round(vm.used / 1e9, 1),
                "percent": vm.percent,
            }}
        if action == "cpu":
            return {"ok": True, "action": action, "data": {
                "percent": psutil.cpu_percent(interval=0.3),
                "cores": psutil.cpu_count(logical=True),
            }}
        if action == "battery":
            bat = psutil.sensors_battery()
            if bat is None:
                return {"ok": False, "error": "No battery detected on this machine."}
            return {"ok": True, "action": action, "data": {
                "percent": bat.percent, "plugged": bat.power_plugged,
            }}
        if action == "processes":
            procs = []
            for p in psutil.process_iter(["pid", "name", "memory"]):
                try:
                    m = p.info.get("memory") or 0
                    procs.append((p.info["name"], p.info["pid"], m / 1e6))
                except Exception:
                    continue
            procs.sort(key=lambda x: x[2], reverse=True)
            return {"ok": True, "action": action, "data": {
                "top": [{"name": n, "pid": pid, "mem_mb": round(mb, 1)}
                        for n, pid, mb in procs[:10]],
            }}
        return {"ok": False, "error": f"Unknown resource action '{action}'. Use ram|cpu|battery|processes."}
    except Exception as exc:
        logger.exception("control_system_resource failed")
        return {"ok": False, "error": str(exc)}


def execute_terminal_command(command: str, timeout_seconds: int = None) -> dict:
    """Execute a shell command (blacklist enforced)."""
    config = get_config()
    safe, reason = check_command_safety(command)
    if not safe:
        return {"ok": False, "error": reason}
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout_seconds or config.terminal_timeout_seconds,
        )
        return {
            "ok": result.returncode == 0,
            "command": command,
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[-4000:],
            "stderr": (result.stderr or "")[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out after {timeout_seconds or config.terminal_timeout_seconds}s."}
    except Exception as exc:
        logger.exception("execute_terminal_command failed")
        return {"ok": False, "error": str(exc)}


_START_APPS_CACHE: list | None = None


def _list_start_apps() -> list:
    """Every app Windows can launch (Store + classic), via Get-StartApps. Cached."""
    global _START_APPS_CACHE
    if _START_APPS_CACHE is None:
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                 "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30)
            data = json.loads(res.stdout) if res.stdout.strip() else []
            _START_APPS_CACHE = [data] if isinstance(data, dict) else data
            logger.info("Get-StartApps: %d apps found", len(_START_APPS_CACHE))
        except Exception:
            logger.exception("Get-StartApps failed")
            return []
    return _START_APPS_CACHE


def _find_start_app(app_name: str) -> dict | None:
    needle = app_name.lower().replace(".exe", "").strip()
    apps = _list_start_apps()
    for a in apps:
        if (a.get("Name") or "").lower() == needle:
            return a
    for a in apps:
        if needle in (a.get("Name") or "").lower():
            return a
    return None


def open_app(app_name: str) -> dict:
    """Open any installed application by name (near-total control model)."""
    app_name = (app_name or "").strip()
    if not app_name:
        return {"ok": False, "error": "No application name provided."}
    try:
        if sys.platform == "win32":
            # 1) Start-menu apps by AppID: covers Microsoft Store apps (WhatsApp...)
            app = _find_start_app(app_name)
            if app and app.get("AppID"):
                # explorer.exe returns exit code 1 even on success: don't check it
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
                return {"ok": True, "app": app.get("Name", app_name),
                        "method": "start apps"}
            # 2) Fuzzy match a Start Menu shortcut
            hit = _find_start_menu_shortcut(app_name)
            if hit:
                subprocess.Popen(f'cmd /c start "" "{hit}"', shell=True)
                return {"ok": True, "app": app_name, "method": "start menu",
                        "shortcut": str(hit)}
            # 3) Last resort: shell 'start' (App Paths registry, URLs)
            res = subprocess.run(
                f'start "" "{app_name}"', shell=True, capture_output=True, text=True)
            if res.returncode == 0:
                return {"ok": True, "app": app_name, "method": "shell start"}
            return {"ok": False, "app": app_name,
                    "error": "Application not found. Say the exact app name "
                             "(e.g. 'WhatsApp', 'Spotify')."}
        if sys.platform == "darwin":
            res = subprocess.run(["open", "-a", app_name], capture_output=True, text=True)
            return {"ok": res.returncode == 0, "app": app_name,
                    "error": res.stderr or None}
        return {"ok": False, "error": f"Unsupported platform {sys.platform}."}
    except Exception as exc:
        logger.exception("open_app failed")
        return {"ok": False, "error": str(exc)}


def _find_start_menu_shortcut(app_name: str) -> Path | None:
    if sys.platform != "win32":
        return None
    needle = app_name.lower().replace(".exe", "")
    roots = [
        Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path("C:/ProgramData/Microsoft/Windows/Start Menu/Programs"),
    ]
    for root in roots:
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            if needle in lnk.stem.lower():
                return lnk
    return None


def interact_gui_app(app_name: str, action: str, payload: str = "") -> dict:
    """Window focus, typing and hotkeys via pyautogui (guarded import)."""
    try:
        import pyautogui  # deferred: optional dependency
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - GUI automation unavailable."}
    try:
        action = (action or "").lower().strip()
        if action == "focus":
            open_app(app_name)
            time.sleep(1.0)
            return {"ok": True, "action": "focus", "app": app_name}
        if action == "type":
            pyautogui.click()
            pyautogui.typewrite(payload, interval=0.02)
            return {"ok": True, "action": "type", "app": app_name}
        if action == "hotkey":
            keys = [k.strip() for k in payload.split("+") if k.strip()]
            pyautogui.hotkey(*keys)
            return {"ok": True, "action": "hotkey", "keys": keys}
        return {"ok": False, "error": f"Unknown GUI action '{action}'. Use focus|type|hotkey."}
    except Exception as exc:
        logger.exception("interact_gui_app failed")
        return {"ok": False, "error": str(exc)}


def manage_files(action: str, source: str, destination: str = "") -> dict:
    """list|move|copy|rename|delete files. Deletes always go to the OS trash."""
    action = (action or "").lower().strip()
    src = Path(source).expanduser() if source else None
    try:
        if not src or not src.exists():
            return {"ok": False, "error": f"Source not found: {source}"}
        if action == "list":
            items = [str(p) for p in src.iterdir()][:200] if src.is_dir() else [str(src)]
            return {"ok": True, "action": action, "items": items}
        if action == "move":
            if not destination:
                return {"ok": False, "error": "move requires a destination."}
            dst = Path(destination).expanduser()
            dst.mkdir(parents=True, exist_ok=True) if dst.suffix == "" else dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return {"ok": True, "action": action, "moved_to": str(dst)}
        if action == "copy":
            if not destination:
                return {"ok": False, "error": "copy requires a destination."}
            dst = Path(destination).expanduser()
            if src.is_dir():
                shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
            return {"ok": True, "action": action, "copied_to": str(dst)}
        if action == "rename":
            if not destination:
                return {"ok": False, "error": "rename requires a new name."}
            dst = Path(destination).expanduser()
            src.rename(dst)
            return {"ok": True, "action": action, "renamed_to": str(dst)}
        if action == "delete":
            try:
                from send2trash import send2trash
                send2trash(str(src))
                return {"ok": True, "action": action, "deleted": str(src), "method": "trash"}
            except ImportError:
                return {"ok": False,
                        "error": "send2trash not installed; permanent deletion is forbidden."}
        return {"ok": False, "error": f"Unknown file action '{action}'."}
    except Exception as exc:
        logger.exception("manage_files failed")
        return {"ok": False, "error": str(exc)}


def download_file(url: str, destination_folder: str = "Downloads") -> dict:
    """Stream a file from a URL to the chosen folder (e.g. a song)."""
    try:
        dest = Path(destination_folder).expanduser()
        if str(dest).lower() == "downloads":
            dest = Path.home() / "Downloads"
        dest.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r"[\\/:*?\"<>|]", "_", url.split("?")[0].rstrip("/").split("/")[-1]) or "download.bin"
        target = dest / filename
        i = 1
        while target.exists():
            target = dest / f"{target.stem} ({i}){target.suffix}"
            i += 1
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with open(target, "wb") as f:
                for chunk in r.iter_bytes(65536):
                    f.write(chunk)
        return {"ok": True, "url": url, "path": str(target),
                "size_mb": round(target.stat().st_size / 1e6, 2)}
    except Exception as exc:
        logger.exception("download_file failed")
        return {"ok": False, "error": str(exc)}


def control_media_player(action: str, media_path: str = "") -> dict:
    """Play/pause/skip media or add a file to the library (skeleton)."""
    action = (action or "").lower().strip()
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - media control unavailable."}
    key_map = {
        "play": "playpause", "pause": "playpause", "next": "nexttrack",
        "previous": "prevtrack", "stop": "stop",
    }
    try:
        if action in key_map:
            pyautogui.press(key_map[action])
            return {"ok": True, "action": action}
        if action == "add_to_library":
            if not media_path:
                return {"ok": False, "error": "add_to_library requires a media file path."}
            open_app("iTunes")
            time.sleep(1.5)
            return {"ok": True, "action": action, "note":
                    "iTunes opened; import of " + media_path + " queued (skeleton)."}
        return {"ok": False, "error": f"Unknown media action '{action}'."}
    except Exception as exc:
        logger.exception("control_media_player failed")
        return {"ok": False, "error": str(exc)}


def send_whatsapp_message(contact: str, message: str) -> dict:
    """Open WhatsApp, select the contact and send the dictated message."""
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - WhatsApp automation unavailable."}
    try:
        open_app("WhatsApp")
        time.sleep(3)
        pyautogui.hotkey("ctrl", "f")          # focus search box
        time.sleep(0.5)
        pyautogui.typewrite(contact, interval=0.03)
        time.sleep(1.5)
        pyautogui.press("enter")               # open best match chat
        time.sleep(1.0)
        pyautogui.typewrite(message, interval=0.03)
        pyautogui.press("enter")               # send
        return {"ok": True, "contact": contact, "message": message}
    except Exception as exc:
        logger.exception("send_whatsapp_message failed")
        return {"ok": False, "error": str(exc)}