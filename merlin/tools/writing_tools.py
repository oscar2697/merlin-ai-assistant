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
import uuid

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
        # Prefer an actual document over the Office start/home screen.
        def title_rank(match):
            title = match[1]
            return (not _is_document_window(title, app_name),
                    0 if title.lower().rstrip().endswith(needle) else 1)
        matches.sort(key=title_rank)
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


def _is_document_window(title: str, app_name: str) -> bool:
    """Distinguish a real document window from Office's Home/loading window."""
    app = (app_name or "").strip().lower()
    if app not in ("word", "microsoft word", "excel", "powerpoint", "onenote"):
        return True
    low = (title or "").lower()
    if re.search(r"\b(?:home|opening|start)\b", low):
        return False
    app_token = "word" if app == "microsoft word" else app
    # A generic title such as "Word" is the start/home window. Windows Office
    # document titles include the document name followed by " - <app>".
    return bool(re.search(rf".+\s[-–—]\s*{re.escape(app_token)}(?:$|\s)", low))


def _wait_for_document_window(app_name: str, timeout: float = 10.0) -> dict | None:
    """Wait until Ctrl+N has produced a document, then focus that window."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        focus = focus_window(app_name)
        if focus.get("ok") and _is_document_window(focus.get("window", ""), app_name):
            return focus
        time.sleep(0.3)
    return None


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


def _write_word_com(text: str) -> dict | None:
    """Create and verify a Word document through Office COM when available.

    Returns None only when COM/Word cannot be acquired, allowing the existing
    keyboard path to remain a fallback. Once a document is created, errors are
    returned rather than creating a second document through that fallback.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None

    pythoncom.CoInitialize()
    document = None
    try:
        try:
            app = win32com.client.GetActiveObject("Word.Application")
        except Exception:
            app = win32com.client.Dispatch("Word.Application")
        app.Visible = True
        document = app.Documents.Add()
    except Exception as exc:
        logger.info("Word COM unavailable; trying keyboard automation: %s", exc)
        pythoncom.CoUninitialize()
        return None

    try:
        document.Content.Text = text
        document.Activate()
        app.Activate()
        actual = str(document.Content.Text or "")
        normalize = lambda value: re.sub(r"\r\n?", "\n", value).strip()
        verified = normalize(actual).startswith(normalize(text))
        if not verified:
            return {"ok": False,
                    "error": "Word no confirmó el contenido del documento nuevo.",
                    "app": "Word", "window": f"{document.Name} - Word",
                    "chars": len(text), "verified": False,
                    "pasted_attempted": True}
        return {"ok": True, "action": "write_text", "app": "Word",
                "window": f"{document.Name} - Word", "chars": len(text),
                "verified": True, "new_document": True}
    except Exception as exc:
        logger.exception("Word COM write failed")
        return {"ok": False, "error": f"No pude escribir en Word: {exc}",
                "app": "Word", "window": getattr(document, "Name", "") + " - Word",
                "chars": len(text), "verified": False,
                "pasted_attempted": True}
    finally:
        pythoncom.CoUninitialize()


def type_into_app(app_name: str, text: str, create_new_document: bool = False) -> dict:
    """Open/focus an app, optionally create a new document, paste Unicode text,
    and verify that the pasted text is present in the active document where possible."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "No text to write."}
    if create_new_document and app_name.lower() in ("word", "microsoft word"):
        word_result = _write_word_com(text)
        if word_result is not None:
            return word_result
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - typing unavailable."}

    focus = focus_window(app_name)
    if not focus.get("ok") and app_name:
        # One instruction should be enough: launch the requested app if it is not open.
        try:
            from merlin.tools.system_tools import open_app
            launched = open_app(app_name)
            if launched.get("ok"):
                deadline = time.monotonic() + 12.0
                while time.monotonic() < deadline:
                    time.sleep(0.4)
                    focus = focus_window(app_name)
                    if focus.get("ok"):
                        break
        except Exception:
            logger.exception("Could not launch app before writing")
    if not focus.get("ok"):
        return {"ok": False, "error": f"No pude abrir o enfocar {app_name}: {focus.get('error', 'ventana no encontrada')}"}

    previous = None
    try:
        previous = _clipboard_get()
        if create_new_document:
            pyautogui.hotkey("ctrl", "n")
            time.sleep(0.45)
            # In Word, Ctrl+N from the Home screen opens the template gallery;
            # Blank document is selected by default. Confirm it with Enter.
            document_focus = _wait_for_document_window(app_name, timeout=1.5)
            if document_focus is None:
                pyautogui.press("enter")
                document_focus = _wait_for_document_window(app_name)
            if document_focus is None:
                return {"ok": False,
                        "error": f"{app_name} no abrió un documento nuevo; no pegué el texto.",
                        "app": app_name, "window": focus.get("window", ""),
                        "chars": len(text), "verified": False,
                        "pasted_attempted": False}
            focus = document_focus
        _clipboard_set(text)
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.8)
        # Verify visible document text when clipboard/UI automation permits it.
        verified = False
        if app_name.lower() in ("word", "microsoft word", "notepad", "bloc de notas", "wordpad"):
            try:
                # Invalidate the expected text before copying. Otherwise a
                # failed Ctrl+C could leave the pasted source text on the
                # clipboard and falsely "verify" an empty document.
                marker = f"MERLIN_VERIFY_{uuid.uuid4().hex}"
                _clipboard_set(marker)
                pyautogui.hotkey("ctrl", "a")
                pyautogui.hotkey("ctrl", "c")
                time.sleep(0.5)
                visible_text = _clipboard_get() or ""
                pyautogui.press("right")  # collapse selection; do not overwrite content
                verified = text[:min(80, len(text))].strip() in visible_text
                if not verified:
                    raise RuntimeError("La aplicación no confirmó que el texto insertado esté presente.")
            except Exception as verify_exc:
                logger.warning("Writing verification unavailable/failed: %s", verify_exc)
                return {"ok": False,
                        "error": f"No pude verificar el texto en {app_name}: {verify_exc}",
                        "app": app_name, "window": focus.get("window", ""),
                        "chars": len(text), "verified": False,
                        "pasted_attempted": True}
        return {"ok": True, "action": "write_text", "app": app_name,
                "window": focus.get("window", ""), "chars": len(text),
                "verified": verified, "new_document": create_new_document}
    except Exception as exc:
        logger.exception("type_into_app failed")
        return {"ok": False, "error": str(exc)}
    finally:
        if previous is not None:
            try:
                _clipboard_set(previous)
            except Exception:
                pass
