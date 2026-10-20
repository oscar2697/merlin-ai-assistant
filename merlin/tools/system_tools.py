"""OS & system control skills (psutil, terminal, apps, files, media, WhatsApp)."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Tuple

import httpx
import psutil

from merlin.config import get_config

logger = logging.getLogger("merlin.tools.system")

# Chrome profile folder to open (see chrome://version -> "Profile Path").
# Examples: "Default", "Profile 1". Can also be set with MERLIN_CHROME_PROFILE.
CHROME_PROFILE = os.environ.get("MERLIN_CHROME_PROFILE", "Profile 1")


# Optional bridge to the GUI cursor. Tool calls still work in CLI/headless mode.
def _cursor_call(name: str, *args, **kwargs):
    try:
        from merlin.gui import cursor_overlay
        fn = getattr(cursor_overlay, name, None)
        if callable(fn):
            return fn(*args, **kwargs)
    except Exception:
        logger.debug("cursor bridge %s unavailable", name, exc_info=True)
    return None


def _cursor_start_point() -> tuple[int, int]:
    try:
        from merlin.gui import cursor_overlay
        fn = getattr(cursor_overlay, "_start_button_point", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    try:
        import ctypes
        return int(ctypes.windll.user32.GetSystemMetrics(0) // 2), int(ctypes.windll.user32.GetSystemMetrics(1) - 26)
    except Exception:
        return 960, 1054


def _cursor_window_point(app_name: str) -> tuple[int, int] | None:
    try:
        from merlin.gui import cursor_overlay
        fn = getattr(cursor_overlay, "_active_window_point", None)
        if callable(fn):
            return fn(app_name)
    except Exception:
        pass
    return None


def check_command_safety(command: str) -> Tuple[bool, str]:
    """Enforce the minimal blacklist of irreversible actions."""
    c = (command or "").lower()
    for bad in get_config().command_blacklist:
        if bad in c:
            return False, f"Blocked: '{bad}' is on the irreversible-action blacklist."
    return True, ""


def _shell_argv(command: str) -> list[str]:
    """Return an explicit platform-shell invocation for a trusted command string."""
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


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
            # "memory_info" is the real psutil attribute ("memory" is not valid)
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    mi = p.info.get("memory_info")
                    m = mi.rss if mi else 0
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
            _shell_argv(command), shell=False, capture_output=True, text=True,
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


# --------------------------------------------------------------------- Chrome
def _find_chrome() -> str | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("chrome")


def _launch_chrome(url: str = "") -> bool:
    """Start Chrome directly in the configured profile (skips the profile picker)."""
    exe = _find_chrome()
    if not exe:
        return False
    cmd = [exe, f"--profile-directory={CHROME_PROFILE}"]
    if url:
        cmd.append(url)
    subprocess.Popen(cmd)
    return True


def search_web(query: str) -> dict:
    """Open a web search and animate the cursor from the wizard to the browser."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "No search query provided."}
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    _cursor_call("task_step", f"Preparando búsqueda: {query[:80]}",
                 _cursor_start_point(), click=True, duration=0.22)
    try:
        if sys.platform == "win32" and _launch_chrome(url):
            result = {"ok": True, "query": query, "url": url, "method": "chrome"}
        else:
            webbrowser.open(url)
            result = {"ok": True, "query": query, "url": url, "method": "default browser"}
        time.sleep(0.35)
        target = _cursor_window_point("chrome") or _cursor_window_point("browser")
        if target:
            _cursor_call("task_step", "Abriendo resultados en el navegador", target,
                         click=True, duration=0.30)
        else:
            _cursor_call("task_step", "Cargando resultados de búsqueda")
        _cursor_call("task_step", "Búsqueda abierta; revisando los resultados")
        return result
    except Exception as exc:
        logger.exception("search_web failed")
        _cursor_call("task_step", f"Error al buscar: {exc}")
        return {"ok": False, "error": str(exc)}


# ----------------------------------------------------------------------- Apps
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


_APP_ALIASES = {
    "itunes": ("iTunes", "Apple iTunes"),
    "word": ("Word", "Microsoft Word"),
    "microsoft word": ("Word", "Microsoft Word"),
    "excel": ("Excel", "Microsoft Excel"),
    "powerpoint": ("PowerPoint", "Microsoft PowerPoint"),
    "bloc de notas": ("Notepad", "Bloc de notas"),
    "notepad": ("Notepad", "Bloc de notas"),
    "spotify": ("Spotify",),
    "chrome": ("Google Chrome", "Chrome"),
    "whatsapp": ("WhatsApp",),
}


def _app_candidates(app_name: str) -> tuple[str, ...]:
    cleaned = (app_name or "").strip().lower().replace(".exe", "")
    aliases = _APP_ALIASES.get(cleaned, ())
    return tuple(dict.fromkeys((app_name.strip(), *aliases))) if app_name.strip() else ()


def _direct_app_path(app_name: str) -> Path | None:
    """Known desktop-app paths; avoids relying only on Start-menu labels."""
    if sys.platform != "win32":
        return None
    key = (app_name or "").strip().lower().replace(".exe", "")
    roots = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files")),
             Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")),
             Path(os.environ.get("LOCALAPPDATA", ""))]
    candidates = []
    if key in ("itunes", "apple itunes"):
        candidates += [r / "iTunes" / "iTunes.exe" for r in roots[:2]]
    elif key in ("word", "microsoft word"):
        candidates += [r / "Microsoft Office" / "root" / "Office16" / "WINWORD.EXE" for r in roots[:2]]
        candidates += [r / "Microsoft Office" / "Office16" / "WINWORD.EXE" for r in roots[:2]]
    elif key in ("excel", "microsoft excel"):
        candidates += [r / "Microsoft Office" / "root" / "Office16" / "EXCEL.EXE" for r in roots[:2]]
    elif key in ("powerpoint", "microsoft powerpoint"):
        candidates += [r / "Microsoft Office" / "root" / "Office16" / "POWERPNT.EXE" for r in roots[:2]]
    return next((p for p in candidates if p.exists()), None)


def _open_app_impl(app_name: str) -> dict:
    """Launch an installed application through the platform launcher."""
    app_name = (app_name or "").strip()
    if not app_name:
        return {"ok": False, "error": "No application name provided."}
    try:
        if sys.platform == "win32":
            candidates = _app_candidates(app_name)
            # Chrome: launch with configured profile, not the profile picker.
            if any("chrome" in c.lower() for c in candidates) and _launch_chrome():
                return {"ok": True, "app": "Google Chrome", "method": "chrome profile",
                        "profile": CHROME_PROFILE}
            # Known desktop executables are more reliable than fuzzy Start-menu search.
            direct = _direct_app_path(app_name)
            if direct:
                subprocess.Popen([str(direct)])
                return {"ok": True, "app": direct.stem, "method": "known executable",
                        "path": str(direct)}
            # Start-menu apps, trying canonical names and aliases.
            for candidate in candidates:
                app = _find_start_app(candidate)
                if app and app.get("AppID"):
                    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
                    return {"ok": True, "app": app.get("Name", candidate),
                            "method": "start apps"}
            # Fuzzy shortcut match; try aliases before the spoken spelling.
            for candidate in candidates:
                hit = _find_start_menu_shortcut(candidate)
                if hit:
                    os.startfile(str(hit))
                    return {"ok": True, "app": candidate, "method": "start menu",
                            "shortcut": str(hit)}
            # Last resort: let Windows resolve App Paths, registered apps and URLs.
            try:
                os.startfile(app_name)
                return {"ok": True, "app": app_name, "method": "shell start"}
            except OSError:
                pass
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



def open_app(app_name: str) -> dict:
    """Open an app and animate MERLIN's independent cursor through the task."""
    name = (app_name or "").strip()
    if not name:
        return {"ok": False, "error": "No application name provided."}
    start = _cursor_start_point()
    _cursor_call("task_step", f"Buscando {name}", start, click=True, duration=0.25)
    result = _open_app_impl(name)
    if result.get("ok"):
        _cursor_call("task_step", f"Abriendo {result.get('app', name)}")
        # The app launcher is API-based for reliability; move the visible agent
        # cursor to the resulting window so the animation reflects the action.
        time.sleep(0.30)
        target = _cursor_window_point(name)
        if target:
            _cursor_call("task_step", f"Enfocando {result.get('app', name)}", target,
                         click=True, duration=0.32)
        else:
            try:
                import ctypes
                target = (ctypes.windll.user32.GetSystemMetrics(0)//2,
                          ctypes.windll.user32.GetSystemMetrics(1)//2)
                _cursor_call("task_step", f"Aplicación abierta: {result.get('app', name)}",
                             target, click=True, duration=0.28)
            except Exception:
                _cursor_call("task_step", f"Aplicación abierta: {result.get('app', name)}")
    else:
        _cursor_call("task_step", f"No pude abrir {name}: {result.get('error', 'error desconocido')}")
    return result


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
        _cursor_call("task_step", f"Acción {action} en {app_name}")
        if action == "focus":
            open_app(app_name)
            time.sleep(1.0)
            return {"ok": True, "action": "focus", "app": app_name}
        if action == "type":
            # Focus the target app first; avoid a coordinate-less click at the
            # wizard's origin. Typing then goes to the foreground app as intended.
            try:
                from pywinauto import Desktop
                active = Desktop(backend="uia").get_active()
                focused = active.get_focus() if active else None
                if focused:
                    rect = focused.rectangle()
                    pt = (int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2))
                    _cursor_call("task_step", f"Seleccionando campo de texto en {app_name}", pt, click=True, duration=0.20)
                    focused.set_focus()
            except Exception:
                logger.debug("could not locate focused input control", exc_info=True)
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


# ------------------------------------------------------------------ Screenshots
def take_screenshot(target: str = "screen") -> dict:
    """Capture the full screen (or a window by partial title) to
    ~/merlin/screenshots/. `target`: "screen"/"pantalla" or a window name
    (e.g. "opencode", "chrome")."""
    try:
        import pyautogui
        from datetime import datetime

        region = None
        if target and target.strip().lower() not in ("screen", "pantalla",
                                                      "full", "ventana"):
            hwnd = None
            try:
                import win32gui
                from merlin.tools import writing_tools
                needle = target.strip().lower()
                for h, title in writing_tools._windows():
                    if needle in title.lower() and not title.lower().startswith("merlin"):
                        hwnd = h
                        break
                if hwnd is None:
                    return {"ok": False,
                            "error": f"No open window matches '{target}'."}
                import win32con
                if win32gui.IsIconic(hwnd):  # minimized: the rect is useless
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.8)
                region = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
            except ImportError:
                region = None  # window capture is Windows-only; full screen instead
        img = pyautogui.screenshot(region=(region[0], region[1],
                                            region[2] - region[0],
                                            region[3] - region[1])
                                    if region else None)
        out_dir = Path.home() / "merlin" / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"merlin_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        img.save(str(path))
        return {"ok": True, "path": str(path),
                "target": target or "screen",
                "size": f"{img.width}x{img.height}"}
    except Exception as exc:
        logger.exception("take_screenshot failed")
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------- Media
def _itunes_running() -> bool:
    try:
        return any((p.info.get("name") or "").lower() == "itunes.exe"
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def _itunes_com_action(action: str, shuffle: bool) -> dict | None:
    """Drive a RUNNING iTunes through COM (it knows whether music is playing, so
    'play' never pauses by accident). Returns None if pywin32 is not installed."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        logger.info("pywin32 not installed - falling back to media keys")
        return None
    pythoncom.CoInitialize()  # COM must be initialised in every thread
    try:
        it = win32com.client.Dispatch("iTunes.Application")
        if action == "play":
            if shuffle:
                it.LibraryPlaylist.Shuffle = True
            if it.PlayerState == 1:
                return {"ok": True, "action": action, "shuffle": shuffle,
                        "method": "iTunes COM", "note": "already playing"}
            if shuffle or it.CurrentTrack is None:
                it.LibraryPlaylist.PlayFirstTrack()
            else:
                it.Play()
        elif action == "pause":
            it.Pause()
        elif action == "next":
            it.NextTrack()
        elif action == "previous":
            it.PreviousTrack()
        elif action == "stop":
            it.Stop()
        return {"ok": True, "action": action, "shuffle": shuffle, "method": "iTunes COM"}
    except Exception as exc:
        logger.exception("iTunes COM failed")
        return {"ok": False, "error": f"iTunes COM error: {exc}"}
    finally:
        pythoncom.CoUninitialize()


# iTunes COM search fields (ITPlaylistSearchField): 0=all, 1=artists, 2=albums, 3=song names
_ITUNES_FIELD = {"all": 0, "artist": 1, "artists": 1, "album": 2, "albums": 2,
                 "song": 3, "songs": 3}


def _itunes_com_play_specific(track_name: str, search_field: str = "all",
                              play: bool = True) -> dict | None:
    """Search the iTunes library and play the best match ('play_track').

    Dispatch launches iTunes automatically if it is closed. Returns None when
    pywin32 is not installed. play=False is a dry run (used by tests).
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        logger.info("pywin32 not installed - cannot search the iTunes library")
        return None
    pythoncom.CoInitialize()
    try:
        it = win32com.client.Dispatch("iTunes.Application")
        field = _ITUNES_FIELD.get((search_field or "all").lower(), 0)
        tracks = it.LibraryPlaylist.Search(track_name, field)
        if (tracks is None or tracks.Count == 0) and field != 0:
            tracks = it.LibraryPlaylist.Search(track_name, 0)  # retry: all fields
        if tracks is None or tracks.Count == 0:
            return {"ok": False, "action": "play_track", "track_name": track_name,
                    "error": f"Nothing found for '{track_name}' in the iTunes library."}
        track = tracks.Item(1)
        if play:
            track.Play()
        return {"ok": True, "action": "play_track", "track_name": track_name,
                "track": track.Name, "artist": track.Artist, "album": track.Album,
                "method": "iTunes COM", "played": play}
    except Exception as exc:
        logger.exception("iTunes COM search failed")
        return {"ok": False, "error": f"iTunes COM error: {exc}"}
    finally:
        pythoncom.CoUninitialize()


def control_media_player(action: str, media_path: str = "", shuffle: bool = False,
                         track_name: str = "", search_field: str = "all") -> dict:
    """Media control: play/pause/skip, shuffle, play a specific song/artist, import."""
    action = (action or "").lower().strip()
    if action == "play_track":
        if not track_name:
            return {"ok": False, "error": "play_track requires a song or artist name."}
        if sys.platform == "win32":
            result = _itunes_com_play_specific(track_name, search_field)
            if result is not None:
                return result
            return {"ok": False,
                    "error": "pywin32 not installed - cannot search the iTunes library."}
        return {"ok": False, "error": "play_track is Windows/iTunes-only for now."}
    if (sys.platform == "win32" and _itunes_running()
            and action in ("play", "pause", "next", "previous", "stop")):
        com = _itunes_com_action(action, bool(shuffle))
        if com is not None:
            return com
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - media control unavailable."}
    # Media keys: 'playpause' is a TOGGLE, it cannot know whether music is playing.
    key_map = {
        "play": "playpause", "pause": "playpause", "next": "nexttrack",
        "previous": "prevtrack", "stop": "stop",
    }
    try:
        if action in key_map:
            pyautogui.press(key_map[action])
            return {"ok": True, "action": action, "method": "media key"}
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
