"""MERLIN's visible cursor: a transparent, click-through, always-on-top overlay that
draws a chibi MERLIN next to the REAL mouse pointer whenever the assistant is
operating the computer (like the AI-agent demos with their own cursor).

How it works
- attach() (called by the GUI) creates the overlay and wraps the pyautogui mouse
  and keyboard functions: every call marks the agent as "active" for a short
  while, so NO tool needs to be modified.
- While active, a ~60 fps loop reads the real pointer position and moves the
  chibi (offset so it never covers the click point). A ripple is drawn on clicks.
- Extra API for tools: agent_mode() context manager, announce()/announce_click()/
  announce_wait() (ping at a screen position), current_position() (real pointer).
- Covers every monitor (virtual screen). Disable with MERLIN_SHOW_CURSOR=0.
  Without a GUI (CLI mode) every call is a no-op.

Art (first one found wins): ~/merlin/cursor.png (transparent PNG, any size),
first frame of ~/merlin/avatar_idle.gif, ~/merlin/avatar.png, then a drawn
mini wizard.
"""
from __future__ import annotations

import contextlib
import logging
import os
import queue
import threading
import time
from pathlib import Path

logger = logging.getLogger("merlin.gui.cursor")

_AMBER = "#e8b64c"
_HOME = Path.home() / "merlin"
_SPRITE_SIZE = 46
_OFFSET = (16, 14)    # sprite top-left relative to the pointer hotspot
_BUBBLE_BG = "#101b31"
_BUBBLE_EDGE = "#69d5ff"
_BUBBLE_FG = "#f2f7ff"
_LINGER = 1.6         # seconds the chibi stays after the last mouse/keyboard action
_FRAME_MS = 16        # ~60 fps while visible
_IDLE_MS = 60         # polling period while hidden

_overlay = None       # CursorOverlay singleton (set by the GUI)
_enabled = os.environ.get("MERLIN_SHOW_CURSOR", "1") == "1"
_lock = threading.Lock()
_depth = 0            # nested agent_mode() contexts
_active_until = 0.0   # monotonic deadline set by pulse()
_click_stamp = 0.0    # monotonic time of the last click-like action
_pings: "queue.Queue[tuple]" = queue.Queue()
_patched = False

_MOUSE_FUNCS = ("moveTo", "moveRel", "move", "click", "leftClick", "rightClick",
                "middleClick", "doubleClick", "tripleClick", "mouseDown",
                "mouseUp", "drag", "dragTo", "dragRel", "scroll")
_KEY_FUNCS = ("press", "keyDown", "keyUp", "typewrite", "write", "hotkey")
_CLICK_FUNCS = frozenset(("click", "leftClick", "rightClick", "middleClick",
                          "doubleClick", "tripleClick", "mouseDown"))


# ------------------------------------------------------------------- public API
def overlay_enabled() -> bool:
    return _enabled


def attach(overlay) -> None:
    """Called by the GUI. Also hooks pyautogui so every mouse/keyboard action
    shows MERLIN's cursor automatically."""
    global _overlay
    _overlay = overlay
    _patch_pyautogui()


def pulse(seconds: float = _LINGER, click: bool = False) -> None:
    """Mark the agent as active for `seconds` (thread-safe, cheap)."""
    global _active_until, _click_stamp
    if not (_enabled and _overlay is not None):
        return
    now = time.monotonic()
    _active_until = max(_active_until, now + seconds)
    if click:
        _click_stamp = now


@contextlib.contextmanager
def agent_mode():
    """Keep MERLIN's cursor visible for the whole block (any thread)."""
    global _depth
    with _lock:
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth = max(0, _depth - 1)
        pulse(0.8)  # short linger after the last step


def announce(x: float, y: float) -> None:
    """Ping a screen position (ripple) without moving the real pointer."""
    if _enabled and _overlay is not None:
        _pings.put((x, y))
        pulse(1.2)


def announce_click(x: float, y: float) -> None:
    announce(x, y)


def narrate(text: str, duration: float = 5.0) -> None:
    """Show a short speech bubble beside MERLIN's cursor avatar.

    Safe to call from worker threads: rendering happens in the Tk polling loop.
    """
    if not (_enabled and _overlay is not None) or not text:
        return
    try:
        _overlay.narrate(str(text), duration=duration)
    except Exception:
        logger.exception("could not display cursor narration")


def announce_wait(x: float, y: float, duration: float = 0.55) -> None:
    """announce + block briefly so the animation is visible before a click."""
    if not (_enabled and _overlay is not None):
        return
    announce(x, y)
    time.sleep(duration)


def current_position() -> tuple | None:
    """Where the REAL mouse pointer is (the avatar's eyes follow it)."""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    except Exception:
        return None


# --------------------------------------------------------------------- pyautogui
def _patch_pyautogui() -> None:
    """Wrap pyautogui's mouse/keyboard functions: each call pulses the overlay."""
    global _patched
    if _patched or not _enabled:
        return
    try:
        import pyautogui
    except ImportError:
        return

    def wrap(name, original, is_click):
        def wrapper(*args, **kwargs):
            pulse()
            try:
                return original(*args, **kwargs)
            finally:
                if is_click:
                    pulse(click=True)  # ripple once the pointer has arrived
        wrapper.__name__ = name
        wrapper.__doc__ = getattr(original, "__doc__", None)
        return wrapper

    for name in _MOUSE_FUNCS + _KEY_FUNCS:
        original = getattr(pyautogui, name, None)
        if callable(original):
            setattr(pyautogui, name, wrap(name, original, name in _CLICK_FUNCS))
    _patched = True


# ------------------------------------------------------------------------ helpers
def _virtual_screen(root) -> tuple:
    """(x, y, width, height) of the whole virtual desktop (all monitors)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        x, y, w, h = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _procedural_chibi(size: int):
    """A tiny wizard drawn with Pillow (supersampled) - used when no art exists."""
    from PIL import Image, ImageDraw

    s = 4
    n = size * s
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([n * 0.22, n * 0.62, n * 0.78, n * 1.06], fill=(35, 42, 61, 255))      # robe
    d.ellipse([n * 0.26, n * 0.34, n * 0.74, n * 0.80], fill=(232, 213, 163, 255))   # face
    d.polygon([(n * 0.32, n * 0.62), (n * 0.68, n * 0.62), (n * 0.5, n * 0.93)],
              fill=(207, 196, 173, 255))                                              # beard
    d.polygon([(n * 0.14, n * 0.42), (n * 0.5, n * 0.02), (n * 0.86, n * 0.42)],
              fill=(47, 111, 237, 255))                                               # hat
    d.rectangle([n * 0.10, n * 0.38, n * 0.90, n * 0.46], fill=(232, 182, 76, 255))  # band
    for ex in (0.40, 0.60):                                                           # eyes
        d.ellipse([n * (ex - 0.045), n * 0.50, n * (ex + 0.045), n * 0.60],
                  fill=(28, 39, 51, 255))
    return img.resize((size, size), Image.LANCZOS)


class CursorOverlay:
    """Draws MERLIN's chibi next to the real pointer while the agent is active."""

    def __init__(self, root) -> None:
        import tkinter as tk

        self.root = root
        self._vx, self._vy, self._vw, self._vh = _virtual_screen(root)
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", "black")
        self.win.configure(bg="black")
        self.win.geometry(f"{self._vw}x{self._vh}{self._vx:+d}{self._vy:+d}")

        self.canvas = tk.Canvas(self.win, width=self._vw, height=self._vh,
                                bg="black", highlightthickness=0, bd=0)
        self.canvas.pack()

        self._sprite = self._load_sprite()  # keep a reference (Tk drops it otherwise)
        self._sprite_id = self.canvas.create_image(
            0, 0, image=self._sprite, anchor="nw", state="hidden")
        self._label_id = self.canvas.create_text(
            0, 0, text="MERLIN", fill=_AMBER, font=("Segoe UI", 8, "bold"),
            anchor="n", state="hidden")
        self._shown = False
        self._styled = False
        self._seen_click = 0.0
        self._narration_text = ""
        self._narration_until = 0.0
        self._bubble_shape_id = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, 0, 0, smooth=True,
            fill=_BUBBLE_BG, outline=_BUBBLE_EDGE, width=2, state="hidden")
        self._bubble_text_id = self.canvas.create_text(
            0, 0, text="", fill=_BUBBLE_FG,
            font=("Segoe UI", 10), anchor="nw", width=270,
            state="hidden")

        self.root.after(_IDLE_MS, self._poll)

    def narrate(self, text: str, duration: float = 5.0) -> None:
        """Schedule a wrapped speech bubble near the avatar."""
        self._narration_text = str(text).strip()
        self._narration_until = time.monotonic() + max(0.8, float(duration))
        pulse(max(0.8, float(duration)))
        # The poll loop updates the canvas; this method may be called by a worker.

    # --------------------------------------------------------------- sprite art
    def _load_sprite(self):
        from PIL import Image, ImageFilter, ImageTk

        img = None
        for path in (_HOME / "cursor.png", _HOME / "avatar_idle.gif",
                     _HOME / "avatar.png"):
            if not path.exists():
                continue
            try:
                with Image.open(path) as src:
                    src.seek(0)
                    img = src.convert("RGBA")
                break
            except Exception:
                logger.exception("could not load cursor art %s", path)
        if img is None:
            img = _procedural_chibi(_SPRITE_SIZE)

        box = img.getchannel("A").getbbox()  # crop transparent margins
        if box:
            img = img.crop(box)
        inner = _SPRITE_SIZE - 6              # room for the outline
        img.thumbnail((inner, inner), Image.LANCZOS)
        canvas = Image.new("RGBA", (_SPRITE_SIZE, _SPRITE_SIZE), (0, 0, 0, 0))
        canvas.paste(img, ((_SPRITE_SIZE - img.width) // 2,
                           (_SPRITE_SIZE - img.height) // 2), img)

        # 1-bit alpha (Tk blends soft alpha against black, which leaves dark
        # fringes on a colour-keyed window) + a dark outline so it stays readable.
        alpha = canvas.getchannel("A").point(lambda a: 255 if a > 110 else 0)
        canvas.putalpha(alpha)
        outline = alpha.filter(ImageFilter.MaxFilter(5))
        base = Image.new("RGBA", canvas.size, (24, 16, 4, 0))
        base.putalpha(outline)
        base.paste(canvas, (0, 0), canvas)
        return ImageTk.PhotoImage(base)

    # ------------------------------------------------------------- click-through
    def _make_click_through(self) -> None:
        """WS_EX_TRANSPARENT | NOACTIVATE | TOOLWINDOW on the real top-level
        window: the mouse never hits the overlay and it never steals focus."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int,
                                              ctypes.c_long]
            child = self.win.winfo_id()
            hwnd = user32.GetAncestor(child, 2) or child  # GA_ROOT
            style = user32.GetWindowLongW(hwnd, -20)      # GWL_EXSTYLE
            user32.SetWindowLongW(
                hwnd, -20,
                style | 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080)
        except Exception:
            logger.exception("could not make the cursor overlay click-through")

    # ------------------------------------------------------------------ main loop
    def _poll(self) -> None:
        try:
            now = time.monotonic()
            with _lock:
                active = _depth > 0
            wanted = active or now < _active_until or now < self._narration_until or not _pings.empty()
            if wanted:
                self._show()
                pos = current_position()
                if pos:
                    px, py = pos[0] - self._vx, pos[1] - self._vy
                    self._place(px, py)
                    if _click_stamp > self._seen_click:
                        self._seen_click = _click_stamp
                        self._ripple(px, py)
                self._drain_pings()
            elif self._shown:
                self._hide()
        except Exception:
            logger.exception("cursor overlay tick failed")
        self.root.after(_FRAME_MS if self._shown else _IDLE_MS, self._poll)

    def _show(self) -> None:
        if self._shown:
            return
        self.win.deiconify()
        self.win.update_idletasks()
        if not self._styled:  # needs a mapped window to find the real HWND
            self._make_click_through()
            self._styled = True
        self.win.lift()
        self.canvas.itemconfigure(self._sprite_id, state="normal")
        self.canvas.itemconfigure(self._label_id, state="normal")
        self._shown = True

    def _hide(self) -> None:
        self.canvas.itemconfigure(self._sprite_id, state="hidden")
        self.canvas.itemconfigure(self._label_id, state="hidden")
        self.canvas.itemconfigure(self._bubble_shape_id, state="hidden")
        self.canvas.itemconfigure(self._bubble_text_id, state="hidden")
        self.win.withdraw()
        self._shown = False

    def _place(self, x: float, y: float) -> None:
        sx, sy = x + _OFFSET[0], y + _OFFSET[1]
        self.canvas.coords(self._sprite_id, sx, sy)
        self.canvas.coords(self._label_id, sx + _SPRITE_SIZE / 2, sy + _SPRITE_SIZE + 1)
        self._place_narration(sx, sy)

    def _place_narration(self, sx: float, sy: float) -> None:
        """Draw a compact rounded bubble above/right of the cursor sprite."""
        if not self._narration_text or time.monotonic() >= self._narration_until:
            self.canvas.itemconfigure(self._bubble_shape_id, state="hidden")
            self.canvas.itemconfigure(self._bubble_text_id, state="hidden")
            return

        # Limit visible text, but preserve word boundaries.
        text = self._narration_text
        if len(text) > 420:
            text = text[:417].rsplit(" ", 1)[0] + "…"
        self.canvas.itemconfigure(self._bubble_text_id, text=text, state="normal")
        self.canvas.itemconfigure(self._bubble_shape_id, state="normal")
        self.canvas.coords(self._bubble_text_id, sx + 5, sy - 69)
        bbox = self.canvas.bbox(self._bubble_text_id)
        if not bbox:
            return
        x1, y1, x2, y2 = bbox
        pad_x, pad_y = 11, 8
        # Keep the bubble inside the virtual desktop and flip below the avatar
        # if it would cross the top edge.
        bx1, by1, bx2, by2 = x1-pad_x, y1-pad_y, x2+pad_x, y2+pad_y
        if by1 < 4:
            shift = (sy + _SPRITE_SIZE + 12) - by1
            by1 += shift; by2 += shift
            self.canvas.coords(self._bubble_text_id, x1, y1 + shift)
        bx1 = max(4, min(bx1, self._vw - (bx2-bx1) - 4))
        bx2 = min(self._vw - 4, bx1 + (x2-x1) + 2*pad_x)
        points = [bx1+10,by1, bx2-10,by1, bx2,by1+10, bx2,by2-10,
                  bx2-10,by2, bx1+10,by2, bx1,by2-10, bx1,by1+10]
        self.canvas.coords(self._bubble_shape_id, *points)

    def _drain_pings(self) -> None:
        while True:
            try:
                x, y = _pings.get_nowait()
            except queue.Empty:
                break
            self._ripple(x - self._vx, y - self._vy)

    def _ripple(self, x: float, y: float) -> None:
        """Expanding amber ripple at (x, y): the visible 'click'."""
        ring = self.canvas.create_oval(x - 8, y - 8, x + 8, y + 8,
                                       outline=_AMBER, width=3)

        def grow(i: int = 0) -> None:
            if i >= 9:
                self.canvas.delete(ring)
                return
            r = 8 + i * 3
            self.canvas.coords(ring, x - r, y - r, x + r, y + r)
            self.canvas.itemconfigure(ring, width=max(1, 3 - i // 3))
            self.root.after(30, grow, i + 1)

        grow()