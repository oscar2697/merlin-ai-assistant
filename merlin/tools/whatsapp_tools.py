"""WhatsApp Desktop: send a message to a specific chat.

Primary method: **in-app search** (keyboard only). The Microsoft Store build
(MSIX) ignores the parameters of whatsapp://send (it just opens the app on the
chat list), so search is the reliable way to open the exact chat there:

    focus -> Ctrl+F -> paste the contact name -> Enter (first result)
    -> Escape -> click the message box (bottom-centre of the window rect)
    -> paste the message -> Enter to send

MERLIN_WHATSAPP_AUTOSEND=0 leaves the message typed in the chat without
sending. MERLIN_WHATSAPP_METHOD=search|deeplink overrides the automatic
choice (Store -> search; win32 installer -> deep link when the phone is known,
because only that build can pre-fill text).

Caveat: if the search finds no chat for the name, the paste would land in
whatever chat was previously open - the flow assumes the contact exists in
the chat list (numbers in whatsapp_contacts.json can still help the deep
link and future WhatsApp Web support).
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from pathlib import Path

from merlin.tools import system_tools, writing_tools

logger = logging.getLogger("merlin.tools.whatsapp")

# Contacts live in ~/merlin/whatsapp_contacts.json; the repo's merlin/ folder
# is checked as a fallback so the file works from a fresh clone too.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_CONTACTS_FILE = Path.home() / "merlin" / "whatsapp_contacts.json"
_CONTACTS_FILE_FALLBACK = _PACKAGE_DIR / "whatsapp_contacts.json"
_WINDOW = "WhatsApp"


def _autosend() -> bool:
    return os.environ.get("MERLIN_WHATSAPP_AUTOSEND", "1") != "0"


def _norm(name: str) -> str:
    """'Mi Mamá' -> 'mama' (lowercase, no accents, no leading determiner)."""
    name = unicodedata.normalize("NFD", (name or "").lower())
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"^(?:mi|mis|el|la|los|las|my)\s+", "", name.strip())
    return re.sub(r"\s+", " ", name).strip()


def _load_contacts() -> dict:
    """{normalised name: (digits-only phone, chat name to search in WhatsApp)}.

    A value may be just a phone string ("mom": "593...") or an object with the
    exact chat name to search ({"mamá": {"phone": "593...", "chat": "mom"}}) -
    useful when the spoken name differs from the chat's display name and a
    naive search would hit a similar-looking group first.
    """
    source = _CONTACTS_FILE if _CONTACTS_FILE.exists() else _CONTACTS_FILE_FALLBACK
    try:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Could not read %s", source)
        return {}
    contacts = {}
    for name, entry in raw.items():
        if isinstance(entry, dict):
            digits = re.sub(r"\D", "", str(entry.get("phone", "")))
            chat = str(entry.get("chat") or name).strip()
        else:
            digits = re.sub(r"\D", "", str(entry))
            chat = str(name).strip()
        if len(digits) >= 8:  # ignore placeholders
            contacts[_norm(name)] = (digits, chat)
    return contacts


def resolve_contact(name: str) -> dict | None:
    """Saved contact for a spoken name: exact match (ignoring case and accents)
    first, then a close match (fuzzy, cutoff 0.85).

    Returns {"name": chat name to search in WhatsApp, "phone": digits} - the
    chat name comes from the contacts file so a spoken "mamá" searches the
    chat the user saved as "mom", never a similar-looking group.
    """
    contacts = _load_contacts()
    key = _norm(name)
    if not key or not contacts:
        return None
    if key in contacts:
        digits, chat = contacts[key]
    else:
        close = difflib.get_close_matches(key, list(contacts), n=1, cutoff=0.85)
        if close:
            digits, chat = contacts[close[0]]
        else:
            # Prefix match: "daniela" -> "daniela santacruz"
            for k, entry in contacts.items():
                if k.startswith(key) or key.startswith(k):
                    digits, chat = entry
                    break
            else:
                return None
    return {"name": chat, "phone": digits}


def search_enabled() -> bool:
    """Whether the in-app search flow is usable (any method except deeplink-only)."""
    return os.environ.get("MERLIN_WHATSAPP_METHOD", "").lower() != "deeplink"


def _is_store_app() -> bool:
    """The Store (MSIX) WhatsApp runs under WindowsApps and ignores
    whatsapp://send parameters; its deep link only opens the app."""
    try:
        import psutil
        for p in psutil.process_iter(["name", "exe"]):
            name = (p.info.get("name") or "").lower()
            if name.startswith("whatsapp"):
                exe = (p.info.get("exe") or "").lower()
                return "windowsapps" in exe or exe == ""
    except Exception:
        pass
    return True  # when in doubt: search (works in both builds)


def _whatsapp_hwnd():
    """HWND of the WhatsApp top-level window (None if not found)."""
    import win32gui
    for hwnd, title in writing_tools._windows():
        if "whatsapp" in title.lower() and not title.lower().startswith("merlin"):
            return hwnd
    return None


def _window_scale(hwnd) -> float:
    """DPI scale factor of the window (1.0 = 96 DPI)."""
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        return (dpi or 96) / 96.0
    except Exception:
        return 1.0


def _click_message_box() -> dict:
    """Click WhatsApp's message input: bottom-centre of its window rect."""
    try:
        import pyautogui
        import win32gui
    except ImportError:
        return {"ok": False, "error": "pyautogui/pywin32 not installed."}
    hwnd = _whatsapp_hwnd()
    if not hwnd:
        return {"ok": False, "error": "No WhatsApp window found to click."}
    left, _top, right, bottom = win32gui.GetWindowRect(hwnd)
    x = left + (right - left) // 2
    y = bottom - 70  # inside the input row (not the bottom edge)
    pyautogui.click((x, y))
    time.sleep(0.3)
    pyautogui.click((x, y))  # second click: make sure the input has focus
    time.sleep(0.4)
    return {"ok": True}


def _focus_whatsapp(timeout: float = 15.0) -> dict:
    """Wait for the WhatsApp window (it may still be starting) and focus it."""
    deadline = time.time() + timeout
    last = {"ok": False, "error": "WhatsApp window not found."}
    while time.time() < deadline:
        last = writing_tools.focus_window(_WINDOW)
        if last.get("ok"):
            return last
        time.sleep(0.8)
    return last


def send_whatsapp_message(contact: str, message: str) -> dict:
    """Send `message` to the WhatsApp chat of `contact`."""
    contact = (contact or "").strip()
    message = (message or "").strip()
    if not contact or not message:
        return {"ok": False, "error": "Hace falta un contacto y un mensaje."}
    if sys.platform != "win32":
        return {"ok": False, "error": "WhatsApp automation is Windows-only for now."}

    method = os.environ.get("MERLIN_WHATSAPP_METHOD", "").lower()
    found = resolve_contact(contact)
    phone = found["phone"] if found else None
    # Search the SAVED chat name when the contacts file has one: a spoken
    # "mamá" must open the chat saved as "mom", never a similar-looking group.
    search_name = found["name"] if found else contact
    if method == "search":
        return _send_via_search(search_name, message, display=contact)
    if method == "deeplink":
        return _send_via_deeplink(contact, message, phone)
    # Automatic choice: the Store build cannot pre-fill via deep link
    if _is_store_app():
        return _send_via_search(search_name, message, display=contact)
    if phone:
        return _send_via_deeplink(contact, message, phone)
    return _send_via_search(search_name, message, display=contact)


def _open_chat(search_name: str) -> dict:
    """Open the chat by its SAVED name via the in-app search (Store-safe)."""
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - WhatsApp automation unavailable."}
    if not writing_tools.focus_window(_WINDOW).get("ok"):
        system_tools.open_app("WhatsApp")
    win = _focus_whatsapp()
    if not win.get("ok"):
        return win
    pyautogui.hotkey("ctrl", "f")                # focus the search box
    time.sleep(0.7)
    res = writing_tools.paste_text(search_name)  # the saved chat name
    if not res.get("ok"):
        return res
    time.sleep(2.0)                              # let the results load
    pyautogui.press("enter")                     # open the first match
    time.sleep(1.2)
    # NOTE: no Escape here - on the Store build, Esc CLOSES the chat that
    # just opened (the search panel closes by itself when a result opens).
    return {"ok": True, "chat": search_name}


def _send_via_search(search_name: str, message: str,
                     display: str = None) -> dict:
    """Keyboard-only flow (works on the Store build): search the chat by its
    SAVED name, open the first result, click the message box, paste, send."""
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - WhatsApp automation unavailable."}
    try:
        res = _open_chat(search_name)
        if not res.get("ok"):
            return res

        res = _click_message_box()               # focus the text input
        if not res.get("ok"):
            return res
        res = writing_tools.paste_text(message)
        if not res.get("ok"):
            return res
        time.sleep(0.5)

        sent = _autosend()
        if sent:
            pyautogui.press("enter")             # send
        return {"ok": True, "contact": display or search_name, "message": message,
                "method": "search", "sent": sent}
    except Exception as exc:
        logger.exception("send_whatsapp_message (search) failed")
        return {"ok": False, "error": str(exc)}


# Call button offsets from the window's top-right corner, in logical pixels
# (96 DPI); multiplied by the window's DPI scale at runtime. Calibrate with
# dry=True (moves the cursor without clicking) and override with
# MERLIN_WA_CALL_RIGHT / MERLIN_WA_CALL_VIDEO_RIGHT / MERLIN_WA_CALL_TOP.
# Measured by pixel analysis at 147% scaling (see calibration script):
# [video icon][phone icon][other icon][window caption buttons]
_CALL_RIGHT_OFFSET = 164    # voice call (phone) icon
_CALL_VIDEO_RIGHT_OFFSET = 223   # video call icon
_CALL_TOP_OFFSET = 62      # chat header row


def _dry_run_enabled(dry: bool) -> bool:
    """Dry run = move the cursor onto the button WITHOUT clicking. Either the
    `dry` argument or MERLIN_WA_CALL_DRY=1 turns it on (the env var is only
    honoured here, so a stale value in the shell keeps blocking the click)."""
    return bool(dry) or os.environ.get("MERLIN_WA_CALL_DRY", "0") == "1"


def _hover_click(x: int, y: int) -> None:
    """Move onto the target, wait for the hover state, then press and release
    with a short hold. WinUI buttons often ignore the instant move+click that
    pyautogui.click() performs."""
    import pyautogui
    pyautogui.moveTo(x - 6, y, duration=0.15)  # approach: generates pointer-move events
    pyautogui.moveTo(x, y, duration=0.25)
    time.sleep(0.35)                           # let the button show its hover state
    pyautogui.mouseDown()
    time.sleep(0.12)
    pyautogui.mouseUp()


def call_whatsapp_contact(contact: str, video: bool = False,
                          dry: bool = False) -> dict:
    """Start a WhatsApp (video) call to `contact`: open the chat via search,
    then click the call button in the chat header.

    Call buttons are position-based (no UIA/deep link exists for calling on
    the Store build). Calibration: dry=True or MERLIN_WA_CALL_DRY=1 - the
    cursor is moved onto the button without clicking; then override the
    offsets with MERLIN_WA_CALL_RIGHT / MERLIN_WA_CALL_VIDEO_RIGHT /
    MERLIN_WA_CALL_TOP if the cursor lands off target.

    The result carries clicked=True/False and the cursor position, and the
    log shows the same, so a "moved but did not call" case can be diagnosed.
    """
    contact = (contact or "").strip()
    if not contact:
        return {"ok": False, "error": "Hace falta un contacto para llamar."}
    if sys.platform != "win32":
        return {"ok": False, "error": "WhatsApp calls are Windows-only for now."}
    try:
        import pyautogui
        import win32gui
    except ImportError:
        return {"ok": False, "error": "pyautogui/pywin32 not installed."}

    dry = _dry_run_enabled(dry)
    found = resolve_contact(contact)
    search_name = found["name"] if found else contact

    try:
        res = _open_chat(search_name)
        if not res.get("ok"):
            return res
        time.sleep(1.0)  # let the chat header settle and become clickable

        # Warm-up click: right after the search, the first click often only
        # dismisses the search/focus state (that is why _click_message_box
        # clicks twice). Clicking the message box is harmless and settles the
        # window before the real click on the call button.
        if os.environ.get("MERLIN_WA_CALL_WARMUP", "1") != "0":
            _click_message_box()

        hwnd = _whatsapp_hwnd()
        if not hwnd:
            return {"ok": False, "error": "No WhatsApp window found."}
        left, top, right, _bottom = win32gui.GetWindowRect(hwnd)
        scale = _window_scale(hwnd)
        right_off = (os.environ.get("MERLIN_WA_CALL_VIDEO_RIGHT" if video
                                     else "MERLIN_WA_CALL_RIGHT"))
        if right_off is None:
            right_off = _CALL_VIDEO_RIGHT_OFFSET if video else _CALL_RIGHT_OFFSET
        top_off = float(os.environ.get("MERLIN_WA_CALL_TOP",
                                       _CALL_TOP_OFFSET))
        x = right - float(right_off) * scale
        y = top + top_off * scale
        cursor = (int(x), int(y))
        logger.info("WhatsApp %s call button at %s (scale %.2f, dry=%s)",
                    "video" if video else "voice", cursor, scale, dry)

        if dry:
            pyautogui.moveTo(cursor[0], cursor[1], duration=0.4)
            return {"ok": True, "contact": contact, "video": video,
                    "method": "dry-run", "clicked": False, "cursor": cursor,
                    "note": "Cursor placed over the call button; no click was "
                            "made (dry run). Unset MERLIN_WA_CALL_DRY and call "
                            "with dry=False to dial."}
        _hover_click(cursor[0], cursor[1])
        return {"ok": True, "contact": contact, "video": video,
                "method": "header click", "clicked": True, "cursor": cursor}
    except Exception as exc:
        logger.exception("call_whatsapp_contact failed")
        return {"ok": False, "error": str(exc)}


def _send_via_deeplink(contact: str, message: str, phone: str | None) -> dict:
    """whatsapp://send?phone=...&text=... Only the win32 build honours the
    parameters (the Store build just opens the chat list)."""
    if not phone:
        return {"ok": False,
                "error": f"No tengo el número de '{contact}'. Añádelo a "
                         f"whatsapp_contacts.json en tu carpeta merlin."}
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed - WhatsApp automation unavailable."}
    try:
        # Opens the exact chat with the text pre-filled (starts WhatsApp if needed)
        os.startfile(f"whatsapp://send?phone={phone}"
                     f"&text={urllib.parse.quote(message)}")
        win = _focus_whatsapp()
        if not win.get("ok"):
            return win
        time.sleep(2.0)  # let the chat load with the text pre-filled
        sent = _autosend()
        if sent:
            pyautogui.press("enter")
        return {"ok": True, "contact": contact, "message": message,
                "method": "deep link", "sent": sent}
    except Exception as exc:
        logger.exception("send_whatsapp_message (deep link) failed")
        return {"ok": False, "error": str(exc)}