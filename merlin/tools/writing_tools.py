"""Write text into another desktop app: focus its window, paste via the clipboard.

pyautogui.typewrite cannot produce accents, ñ or ¿ and types into whichever
window has focus. Pasting handles any Unicode text, and the window is focused
first by its title. Windows only (pywin32).
"""
from __future__ import annotations

import logging
import re
import sys
import time

logger = logging.getLogger("merlin.tools.writing")

_OWN_TITLE = "merlin"  # the GUI window title starts with "MERLIN"


def _windows() -> list:
    """Visible top-level windows with a title: [(hwnd, title), ...]."""
    import win32gui

    found: list = []

    def collect(hwnd, _extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                found.append((hwnd, title))
        return True

    win32gui.EnumWindows(collect, None)
    return found


def focus_window(app_name: str = "") -> dict:
    """Bring the window whose title contains `app_name` to the foreground.

    With an empty name: if MERLIN has focus, hop back to the previous window
    (Alt+Tab); otherwise keep the current one."""
    if sys.platform != "win32":
        return {"ok": False, "error": "Window focus is Windows-only for now."}
    try:
        import win32api
        import win32con
        import win32gui
    except ImportError:
        return {"ok": False, "error": "pywin32 not installed (pip install pywin32)."}

    needle = (app_name or "").strip().lower()
    try:
        if not needle:
            title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            if title.lower().startswith(_OWN_TITLE):
                import pyautogui
                pyautogui.hotkey("alt", "tab")  # back to the window the user was in
                time.sleep(0.7)
                title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            return {"ok": True, "window": title}

        pattern = re.compile(rf"\b{re.escape(needle)}\b")
        matches = [(h, t) for h, t in _windows()
                   if pattern.search(t.lower()) and not t.lower().startswith(_OWN_TITLE)]
        if not matches:
            return {"ok": False,
                    "error": f"No open window found for '{app_name}'. Open it first."}
        # Prefer titles like "Document1 - Word" (app name at the end)
        matches.sort(key=lambda m: 0 if m[1].lower().rstrip().endswith(needle) else 1)
        hwnd, title = matches[0]
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # Windows refuses SetForegroundWindow from background processes; a
        # synthetic Alt press lifts that lock.
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        return {"ok": True, "window": title}
    except Exception as exc:
        logger.exception("focus_window failed")
        return {"ok": False, "error": f"Could not focus '{app_name}': {exc}"}


def _open_clipboard() -> bool:
    import win32clipboard

    for _ in range(10):  # another process may hold the clipboard for a moment
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.1)
    return False


def _clipboard_get() -> str | None:
    import win32clipboard
    import win32con

    if not _open_clipboard():
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return None
    finally:
        win32clipboard.CloseClipboard()


def _clipboard_set(text: str) -> None:
    import win32clipboard
    import win32con

    if not _open_clipboard():
        raise RuntimeError("The clipboard is busy.")
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def paste_text(text: str) -> dict:
    """Paste `text` into whatever control currently has focus (any Unicode).

    The caller is responsible for focusing the right window first
    (focus_window / type_into_app handle that for whole-app writing).
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "No text to paste."}
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - pasting unavailable."}
    try:
        _clipboard_set(text)
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)  # let the app finish processing the paste
        return {"ok": True, "chars": len(text)}
    except Exception as exc:
        logger.exception("paste_text failed")
        return {"ok": False, "error": str(exc)}


def type_into_app(app_name: str, text: str) -> dict:
    """Focus the app's window and paste `text` at the cursor (any Unicode)."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "No text to write."}
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - typing unavailable."}

    focus = focus_window(app_name)
    if not focus.get("ok"):
        return focus

    previous = None
    try:
        previous = _clipboard_get()
        _clipboard_set(text)
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.8)  # let the app finish pasting before the clipboard is restored
        return {"ok": True, "action": "write_text", "app": app_name,
                "window": focus.get("window", ""), "chars": len(text)}
    except Exception as exc:
        logger.exception("type_into_app failed")
        return {"ok": False, "error": str(exc)}
    finally:
        if previous is not None:
            try:
                _clipboard_set(previous)
            except Exception:
                pass