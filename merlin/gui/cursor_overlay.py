"""MERLIN's visible cursor: a transparent, click-through, always-on-top overlay that
draws a SECOND, independent agent pointer while MERLIN operates the computer.
The user's normal Windows pointer remains where the user left it in independent mode.

How it works
- attach() (called by the GUI) creates the overlay and wraps the pyautogui mouse
  and keyboard functions: every call marks the agent as "active" for a short
  while, so NO tool needs to be modified.
- In independent mode, pyautogui.moveTo/moveRel update a logical agent pointer;
  clicks use Windows UI Automation (pywinauto) without moving the user's pointer.
  The overlay animates from the avatar's taskbar position to the target.
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
from collections import deque
import logging
import math
import os
import queue
import threading
import time
import textwrap
from pathlib import Path

logger = logging.getLogger("merlin.gui.cursor")

_AMBER = "#e8b64c"
_HOME = Path.home() / "merlin"
_SPRITE_SIZE = 42
_OFFSET = (0, 0)      # agent-arrow hotspot is the top-left of its transparent PNG
_CURSOR_MODE = os.environ.get("MERLIN_CURSOR_MODE", "independent").strip().lower()
_INDEPENDENT = _CURSOR_MODE == "independent"
_agent_lock = threading.RLock()
_agent_pos: tuple[float, float] | None = None
_agent_target: tuple[float, float] | None = None
_agent_move_from: tuple[float, float] | None = None
_agent_move_started = 0.0
_agent_move_duration = 0.0
_agent_button_down: tuple[float, float, str] | None = None
_agent_origin: tuple[float, float] | None = None
_move_queue = deque()
_native_saved_pos: tuple[int, int] | None = None
_native_hidden = False
_native_hide_calls = 0
_native_drag_active = False
_task_active = False
_cursor_engaged = False  # True only while an actual desktop action is being performed
_LINGER = 0.85         # seconds the chibi stays after the last mouse/keyboard action
_FRAME_MS = 16        # ~60 fps while visible
_IDLE_MS = 20         # fast wake-up so the cursor appears with the first action

_overlay = None       # CursorOverlay singleton (set by the GUI)
_enabled = os.environ.get("MERLIN_SHOW_CURSOR", "1") == "1"
_lock = threading.Lock()
_depth = 0            # nested agent_mode() contexts
_active_until = 0.0   # monotonic deadline set by pulse()
_click_stamp = 0.0    # monotonic time of the last click-like action
_pings: "queue.Queue[tuple]" = queue.Queue()
_ui_events: "queue.Queue[tuple]" = queue.Queue()
_patched = False
_pyautogui_module = None
_pyautogui_originals = {}
_pyautogui_wrappers = {}
_pyautogui_patch_lock = threading.RLock()
_input_card = None      # InputCard: the interactive right-side "write" card
_input_handler = None   # callback(text) invoked when the user submits from it

_MOUSE_FUNCS = ("moveTo", "moveRel", "move", "click", "leftClick", "rightClick",
                "middleClick", "doubleClick", "tripleClick", "mouseDown",
                "mouseUp", "drag", "dragTo", "dragRel", "scroll", "position")
_KEY_FUNCS = ("press", "keyDown", "keyUp", "typewrite", "write", "hotkey")
_CLICK_FUNCS = frozenset(("click", "leftClick", "rightClick", "middleClick",
                          "doubleClick", "tripleClick", "mouseDown"))

_STATE_LABELS = {
    "idle": "Listo",
    "listening": "Te escucho",
    "hearing": "Te escucho",
    "recording": "Escuchando",
    "transcribing": "Entendiendo voz",
    "thinking": "Pensando",
    "working": "Trabajando",
    "executing": "Ejecutando tarea",
    "browsing": "Investigando",
    "searching": "Buscando información",
    "speaking": "Hablando",
    "error": "Necesito ayuda",
}


# ------------------------------------------------------------------- public API
def overlay_enabled() -> bool:
    return _enabled


def attach(overlay) -> None:
    """Called by the GUI. Also hooks pyautogui so every mouse/keyboard action
    shows MERLIN's cursor automatically."""
    global _overlay, _input_card
    _overlay = overlay
    try:
        _input_card = InputCard(overlay.root, _dispatch_input)
    except Exception:
        logger.exception("could not create the right-side write card")
        _input_card = None
    _patch_pyautogui()


def set_input_handler(fn) -> None:
    """Register the callback invoked when the user sends from the write card."""
    global _input_handler
    _input_handler = fn


def _dispatch_input(text: str) -> None:
    fn = _input_handler
    if fn is None:
        return
    try:
        fn(text)
    except Exception:
        logger.exception("write card submit failed")


def open_input() -> None:
    """Show the interactive right-side write card (avatar click). No dashboard."""
    if _input_card is not None:
        _input_card.show()


def narrate(text: str, duration: float = 5.0) -> None:
    """Module-level compatibility API used by GUI and agent tools."""
    if _overlay is not None:
        _overlay.narrate(text, duration=duration)


def set_state(state: str, detail: str | None = None) -> None:
    """Publish a UI state to the cursor overlay, if it is attached."""
    if _overlay is not None:
        _overlay.set_state(state, detail)


def pulse(seconds: float = _LINGER, click: bool = False) -> None:
    """Mark the agent as active for `seconds` (thread-safe, cheap)."""
    global _active_until, _click_stamp, _cursor_engaged
    if not (_enabled and _overlay is not None):
        return
    _cursor_engaged = True
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
    """Move/ping MERLIN's own cursor at a target without moving the user's mouse."""
    if _enabled and _overlay is not None:
        if _INDEPENDENT:
            move_agent_to(x, y, duration=0.22)
        _pings.put((x, y))
        pulse(1.2)


def announce_click(x: float, y: float) -> None:
    announce(x, y)


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


def agent_position() -> tuple[float, float] | None:
    """Current position of MERLIN's independent pointer, not the user's mouse."""
    with _agent_lock:
        return _agent_pos or _agent_target


def set_agent_origin(x: float, y: float) -> None:
    """Set the visual origin from which MERLIN's cursor emerges (the wizard)."""
    global _agent_pos, _agent_target, _agent_move_from, _agent_origin
    with _agent_lock:
        _agent_origin = (float(x), float(y))
        if _agent_pos is None:
            _agent_pos = _agent_origin
            _agent_target = _agent_origin
            _agent_move_from = _agent_origin
    # Registering the wizard origin must not make the cursor appear by itself.


def _current_agent_position_locked(now: float | None = None):
    now = time.monotonic() if now is None else now
    if _agent_target is None:
        return _agent_pos
    if _agent_move_duration <= 0:
        return _agent_target
    t = max(0.0, min(1.0, (now - _agent_move_started) / _agent_move_duration))
    eased = t * t * (3.0 - 2.0 * t)
    start = _agent_move_from or _agent_pos or _agent_target
    return (start[0] + (_agent_target[0] - start[0]) * eased,
            start[1] + (_agent_target[1] - start[1]) * eased)


def _natural_move_duration(start, target, requested: float) -> float:
    """Scale long traversals so the agent glides instead of teleporting."""
    requested = max(0.0, min(float(requested), 4.0))
    distance = math.hypot(target[0] - start[0], target[1] - start[1])
    if distance < 2 or requested == 0:
        return requested
    # About 1.2 s across a 1600 px desktop, but keep small UI moves snappy.
    return min(1.25, max(requested, distance / 1350.0))


def move_agent_to(x: float, y: float, duration: float = 0.25, *, enqueue: bool = True) -> float:
    """Animate MERLIN's pointer and return the effective, distance-aware duration."""
    global _agent_pos, _agent_target, _agent_move_from, _agent_move_started, _agent_move_duration, _cursor_engaged
    _cursor_engaged = True
    target = (float(x), float(y))
    duration = max(0.0, min(float(duration), 4.0))
    with _agent_lock:
        now = time.monotonic()
        current = _current_agent_position_locked(now) or _agent_origin or current_position() or target
        moving = (_agent_target is not None and _agent_move_duration > 0
                  and now - _agent_move_started < _agent_move_duration)
        if moving and enqueue:
            start = _move_queue[-1][:2] if _move_queue else (_agent_target or current)
            duration = _natural_move_duration(start, target, duration)
            _move_queue.append((target[0], target[1], duration))
        else:
            if not enqueue:
                _move_queue.clear()
            duration = _natural_move_duration(current, target, duration)
            _agent_pos = (float(current[0]), float(current[1]))
            _agent_move_from = _agent_pos
            _agent_target = target
            _agent_move_started = now
            _agent_move_duration = duration
            if duration == 0:
                _agent_pos = target
    _ui_events.put(("move", target[0], target[1], duration))
    pulse(max(1.0, duration + 0.8))
    return duration


def _screen_size() -> tuple[int, int]:
    try:
        import ctypes
        return int(ctypes.windll.user32.GetSystemMetrics(0)), int(ctypes.windll.user32.GetSystemMetrics(1))
    except Exception:
        if _overlay is not None:
            return int(_overlay.root.winfo_screenwidth()), int(_overlay.root.winfo_screenheight())
        return 1920, 1080


def _start_button_point() -> tuple[int, int]:
    """Find the Windows Start button when UIA exposes it; use a safe visual fallback."""
    try:
        from pywinauto import Desktop
        taskbar = Desktop(backend="uia").window(class_name="Shell_TrayWnd")
        for control in taskbar.descendants():
            name = str(getattr(control.element_info, "name", "") or "").lower()
            ctype = str(getattr(control.element_info, "control_type", "") or "")
            if ctype == "Button" and (name in {"start", "start button", "inicio", "botón inicio", "boton inicio"}):
                r = control.rectangle()
                return (int((r.left+r.right)/2), int((r.top+r.bottom)/2))
    except Exception:
        pass
    w, h = _screen_size()
    # Windows 11 normally centres Start; Windows 10 normally left-aligns it.
    return (w // 2, h - 26)


def _active_window_point(title_hint: str = "") -> tuple[int, int] | None:
    """Return a useful point in a newly opened window, without moving the real pointer."""
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="uia")
        hint = (title_hint or "").lower().replace(".exe", "")
        windows = desktop.windows()
        # Prefer matching title, then the active foreground window.
        candidates = []
        for win in windows:
            try:
                title = (win.window_text() or "").lower()
                if title and (not hint or hint in title):
                    candidates.append(win)
            except Exception:
                continue
        if not candidates:
            active = desktop.get_active()
            candidates = [active] if active is not None else []
        if candidates:
            r = candidates[-1].rectangle()
            return int((r.left+r.right)/2), int(r.top + min(54, max(24, (r.bottom-r.top)//8)))
    except Exception:
        pass
    return None


def task_start(title: str) -> None:
    """Begin a request without showing the cursor until a real desktop action occurs."""
    global _task_active, _cursor_engaged, _agent_pos, _agent_target, _agent_move_from
    global _agent_move_started, _agent_move_duration
    title = str(title or "Tarea de MERLIN").strip()[:180]
    _task_active = True
    _cursor_engaged = False
    _ui_events.put(("panel_clear",))
    # Every new action starts visually at the wizard, not at the previous target.
    if _agent_origin is not None:
        with _agent_lock:
            _move_queue.clear()
            _agent_pos = _agent_origin
            _agent_target = _agent_origin
            _agent_move_from = _agent_origin
            _agent_move_started = time.monotonic()
            _agent_move_duration = 0.0


def task_step(message: str, target: tuple[float, float] | None = None,
              *, click: bool = False, duration: float = 0.28) -> None:
    """Brief cursor narration for real actions; never add operational steps to the panel."""
    msg = str(message or "").strip()
    # Activate the overlay before announcing/moving. This makes the cursor appear
    # at the start of the action, not after the first click or wait.
    pulse(max(1.0, min(float(duration) + 0.75, 3.0)))
    if msg and _task_active:
        narrate(msg, duration=2.8)
    if target is not None:
        x, y = float(target[0]), float(target[1])
        effective_duration = move_agent_to(x, y, duration=duration)
        if effective_duration > 0:
            time.sleep(min(effective_duration, 1.25))
        if click:
            _pings.put((x, y))
            pulse(0.8, click=True)


def task_result(message: str) -> None:
    """Publish only the final answer/result to the right-side results card."""
    msg = str(message or "").strip()
    if msg:
        # Final answers and research findings belong exclusively in the right
        # results panel. Cursor bubbles are reserved for brief action dialogue.
        _ui_events.put(("task_result", msg[:2400]))


def task_finish(message: str | None = None) -> None:
    """Finish interaction and return the cursor to the wizard; keep only the result card."""
    global _task_active, _active_until, _cursor_engaged
    if message:
        task_result(message)
    _task_active = False
    _ui_events.put(("task_finish",))
    if _agent_origin is not None and _cursor_engaged:
        move_agent_to(*_agent_origin, duration=0.26)
        _active_until = time.monotonic() + 0.65
    else:
        _active_until = 0.0
    # The return-to-avatar animation is the final visible motion.
    _cursor_engaged = bool(_agent_origin is not None and _active_until > 0)


def announce(x: float, y: float) -> None:
    """Move MERLIN's own cursor to a target without moving the user's visible pointer."""
    if _enabled and _overlay is not None:
        move_agent_to(x, y, duration=0.22)
        _pings.put((x, y))
        pulse(1.2)


def announce_click(x: float, y: float) -> None:
    announce(x, y)
    pulse(click=True)


def announce_wait(x: float, y: float, duration: float = 0.55) -> None:
    announce(x, y)
    time.sleep(max(0.0, duration))


def current_position() -> tuple | None:
    """Where the REAL mouse pointer is; the user's pointer is restored after agent input."""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    except Exception:
        return None


def agent_position() -> tuple | None:
    """Current position of MERLIN's independent visual pointer."""
    with _agent_lock:
        return _current_agent_position_locked() or _agent_origin


@contextlib.contextmanager
def _native_pointer_hidden():
    """Hide the native pointer for an agent action and restore its position."""
    global _native_saved_pos, _native_hidden, _native_hide_calls
    if os.name != "nt":
        yield
        return
    import ctypes
    user32 = ctypes.windll.user32
    outermost = not _native_hidden
    if outermost:
        _native_saved_pos = current_position()
        _native_hide_calls = 0
        try:
            for _ in range(8):
                result = user32.ShowCursor(False)
                _native_hide_calls += 1
                if result < 0:
                    break
            _native_hidden = True
        except Exception:
            _native_saved_pos = None
            _native_hide_calls = 0
            _native_hidden = False
    try:
        yield
    finally:
        if outermost and _native_hidden:
            try:
                if _native_saved_pos:
                    user32.SetCursorPos(int(_native_saved_pos[0]), int(_native_saved_pos[1]))
            finally:
                for _ in range(_native_hide_calls):
                    user32.ShowCursor(True)
                _native_hidden = False
                _native_hide_calls = 0
                _native_saved_pos = None


@contextlib.contextmanager
def _temporarily_unpatch_pyautogui():
    """Let pyautogui's own functions call one another without re-entering wrappers."""
    module = _pyautogui_module
    if module is None:
        yield
        return
    with _pyautogui_patch_lock:
        try:
            for name, original in _pyautogui_originals.items():
                setattr(module, name, original)
            yield
        finally:
            for name, wrapper in _pyautogui_wrappers.items():
                setattr(module, name, wrapper)


def _patch_pyautogui() -> None:
    """Route existing MERLIN mouse tools through a visible independent cursor.

    The real Windows pointer is hidden only during each mouse operation and restored
    afterwards. This preserves compatibility with apps that don't expose UIA. Set
    MERLIN_CURSOR_MODE=legacy to disable the independent-pointer behaviour.
    """
    global _patched, _agent_button_down, _native_drag_active
    global _pyautogui_module, _pyautogui_originals, _pyautogui_wrappers
    if _patched or not _enabled:
        return
    try:
        import pyautogui
    except ImportError:
        logger.warning("pyautogui not installed; cursor movement integration is inactive")
        return
    _pyautogui_module = pyautogui

    def pair_from_args(args, kwargs, default=None):
        x = kwargs.get("x", args[0] if len(args) > 0 else None)
        y = kwargs.get("y", args[1] if len(args) > 1 else None)
        if y is None and isinstance(x, (tuple, list)) and len(x) >= 2:
            x, y = x[0], x[1]
        if x is None or y is None:
            return default
        try:
            return float(x), float(y)
        except (TypeError, ValueError):
            return default

    def wrapper_for(name, original, is_click):
        def wrapper(*args, **kwargs):
            global _native_drag_active, _agent_button_down
            if not _INDEPENDENT:
                pulse()
                try:
                    return original(*args, **kwargs)
                finally:
                    if is_click:
                        pulse(click=True)

            if name == "position":
                return agent_position() or current_position()

            # If a click/scroll has no explicit target, preserve pyautogui's
            # documented semantics (the current real pointer position), but move
            # MERLIN's visual cursor there first rather than clicking at the avatar.
            explicit_point = pair_from_args(args, kwargs)
            point = explicit_point
            if name in ("moveTo", "move", "click", "leftClick", "rightClick",
                        "middleClick", "doubleClick", "tripleClick", "scroll",
                        "dragTo", "dragRel") and point is None:
                if name in ("moveTo", "move"):
                    return original(*args, **kwargs)
                point = agent_position() or current_position()

            if name in ("drag", "dragRel"):
                base = agent_position() or current_position() or (0.0, 0.0)
                dx = kwargs.get("xOffset", args[0] if len(args) > 0 else 0.0)
                dy = kwargs.get("yOffset", args[1] if len(args) > 1 else 0.0)
                try:
                    point = (float(base[0]) + float(dx), float(base[1]) + float(dy))
                except (TypeError, ValueError):
                    point = base
            duration = kwargs.get("duration", args[2] if len(args) > 2 and name in ("moveTo", "move", "drag", "dragTo", "dragRel") else 0.22)
            try:
                duration = max(0.0, min(float(duration), 3.0))
            except Exception:
                duration = 0.22

            if name in ("moveTo", "move"):
                # Show the cursor at the beginning of the movement, not after a click.
                pulse(max(1.0, min(duration + 0.75, 3.0)))
                if point is not None:
                    move_agent_to(point[0], point[1], duration=duration, enqueue=False)
                # During a drag sequence, keep native input hidden and at the agent location.
                if _native_drag_active:
                    with _temporarily_unpatch_pyautogui():
                        return original(*args, **kwargs)
                with _native_pointer_hidden():
                    with _temporarily_unpatch_pyautogui():
                        return original(*args, **kwargs)

            if name == "moveRel":
                pulse(max(1.0, min(duration + 0.75, 3.0)))
                base = agent_position() or current_position() or (0.0, 0.0)
                rel = pair_from_args(args, kwargs, (0.0, 0.0))
                target = (base[0] + rel[0], base[1] + rel[1])
                move_agent_to(*target, duration=duration, enqueue=False)
                if _native_drag_active:
                    with _temporarily_unpatch_pyautogui():
                        return original(*args, **kwargs)
                with _native_pointer_hidden():
                    try:
                        import ctypes
                        ctypes.windll.user32.SetCursorPos(int(base[0]), int(base[1]))
                    except Exception:
                        pass
                    with _temporarily_unpatch_pyautogui():
                        return original(*args, **kwargs)

            if name in ("click", "leftClick", "rightClick", "middleClick", "doubleClick", "tripleClick"):
                pulse(1.4, click=False)
                if point is not None:
                    travel = move_agent_to(point[0], point[1], duration=0.18, enqueue=False)
                    time.sleep(min(travel, 1.25))
                # Explicit coordinates are kept intact; for click() with no target,
                # resolve to MERLIN's logical position rather than the avatar origin.
                if explicit_point is None and point is not None:
                    kwargs = dict(kwargs)
                    kwargs["x"] = int(point[0])
                    kwargs["y"] = int(point[1])
                    args = ()
                with _native_pointer_hidden():
                    if point is not None:
                        try:
                            import ctypes
                            ctypes.windll.user32.SetCursorPos(int(point[0]), int(point[1]))
                        except Exception:
                            pass
                    with _temporarily_unpatch_pyautogui():
                        result = original(*args, **kwargs)
                pulse(click=True)
                return result

            if name == "mouseDown":
                if point is not None:
                    travel = move_agent_to(point[0], point[1], duration=0.16, enqueue=False)
                    time.sleep(min(travel, 1.25))
                if not _native_drag_active:
                    # Keep the native pointer hidden until mouseUp so a drag is real.
                    global _native_saved_pos, _native_hidden
                    try:
                        import ctypes
                        user32 = ctypes.windll.user32
                        _native_saved_pos = current_position()
                        for _ in range(8):
                            if user32.ShowCursor(False) < 0:
                                break
                        _native_hidden = True
                        _native_drag_active = True
                        if point is not None:
                            user32.SetCursorPos(int(point[0]), int(point[1]))
                    except Exception:
                        _native_drag_active = False
                _agent_button_down = (point[0], point[1], str(kwargs.get("button", "left"))) if point else None
                with _temporarily_unpatch_pyautogui():
                    return original(*args, **kwargs)

            if name == "mouseUp":
                try:
                    with _temporarily_unpatch_pyautogui():
                        result = original(*args, **kwargs)
                finally:
                    if _native_drag_active:
                        try:
                            import ctypes
                            user32 = ctypes.windll.user32
                            if _native_saved_pos:
                                user32.SetCursorPos(int(_native_saved_pos[0]), int(_native_saved_pos[1]))
                            for _ in range(_native_hide_calls):
                                user32.ShowCursor(True)
                        except Exception:
                            pass
                        _native_drag_active = False
                        _native_hidden = False
                        _native_hide_calls = 0
                        _native_saved_pos = None
                    _agent_button_down = None
                pulse(click=True)
                return result

            if name in ("drag", "dragTo", "dragRel", "scroll"):
                pulse(max(1.0, min(duration + 0.75, 3.0)))
                if point is not None:
                    move_agent_to(point[0], point[1], duration=duration, enqueue=False)
                if _native_drag_active:
                    with _temporarily_unpatch_pyautogui():
                        return original(*args, **kwargs)
                with _native_pointer_hidden():
                    try:
                        import ctypes
                        if name in ("drag", "dragTo", "dragRel"):
                            # Drags begin at MERLIN's current logical location.
                            start_point = agent_position() or point
                            if start_point:
                                ctypes.windll.user32.SetCursorPos(int(start_point[0]), int(start_point[1]))
                        elif name == "scroll" and explicit_point is None and point is not None:
                            ctypes.windll.user32.SetCursorPos(int(point[0]), int(point[1]))
                    except Exception:
                        pass
                    with _temporarily_unpatch_pyautogui():
                        result = original(*args, **kwargs)
                if name != "scroll":
                    pulse(click=True)
                return result

            # Keyboard events do not move the user's pointer. Keep normal semantics.
            pulse()
            return original(*args, **kwargs)

        wrapper.__name__ = name
        wrapper.__doc__ = getattr(original, "__doc__", None)
        return wrapper

    for name in _MOUSE_FUNCS + _KEY_FUNCS:
        original = getattr(pyautogui, name, None)
        if callable(original):
            wrapper = wrapper_for(name, original, name in _CLICK_FUNCS)
            _pyautogui_originals[name] = original
            _pyautogui_wrappers[name] = wrapper
            setattr(pyautogui, name, wrapper)
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


class InputCard:
    """Interactive 'write to MERLIN' card matching the right-side results panel.

    The cursor overlay is click-through and covers the whole desktop by design
    (it must never steal clicks), so it cannot host a text field. This small,
    borderless, always-on-top card reuses the results-panel look and hosts a
    real Entry + Send button at the same top-right spot. On submit it hides and
    the answer appears in the results panel.
    """

    _W = 470
    _H = 156

    def __init__(self, root, on_submit) -> None:
        import tkinter as tk

        self.root = root
        self.on_submit = on_submit
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", "black")
        self.win.configure(bg="black")

        self.canvas = tk.Canvas(self.win, width=self._W, height=self._H,
                                bg="black", highlightthickness=0, bd=0)
        self.canvas.pack()
        self._draw_card()

        self.entry = tk.Entry(
            self.win, bg="#112640", fg="#e3edf9", insertbackground="#59c9ff",
            relief="flat", highlightthickness=1, highlightbackground="#29445f",
            highlightcolor="#59c9ff", font=("Segoe UI", 12))
        self.entry.place(x=20, y=82, width=self._W - 128, height=38)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda _e: self.hide())

        self.button = tk.Button(
            self.win, text="Enviar", command=self._submit, bg="#397fae",
            fg="#eaf6ff", activebackground="#59c9ff", activeforeground="#04121f",
            relief="flat", borderwidth=0, font=("Segoe UI", 11, "bold"),
            cursor="hand2")
        self.button.place(x=self._W - 100, y=82, width=80, height=38)

        self.canvas.bind("<Button-1>", lambda _e: self.entry.focus_set())

    def _draw_card(self) -> None:
        c = self.canvas
        w, h = self._W, self._H

        def round_points(x1, y1, x2, y2, r):
            pts = []
            corners = ((x2 - r, y2 - r, 0.0), (x1 + r, y2 - r, 90.0),
                       (x1 + r, y1 + r, 180.0), (x2 - r, y1 + r, 270.0))
            for cx, cy, start in corners:
                for i in range(0, 91, 6):
                    a = math.radians(start + i)
                    pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
            return pts

        c.create_polygon(round_points(5, 6, w + 5, h + 6, 18), smooth=True,
                         fill="#020713", outline="")
        c.create_polygon(round_points(0, 0, w, h, 18), smooth=True,
                         fill="#0a1425", outline="#397fae", width=1.5)
        c.create_polygon(round_points(1, 1, w - 1, 56, 17), smooth=True,
                         fill="#112640", outline="")
        c.create_oval(16, 14, 42, 40, fill="#173c60", outline="#59c9ff", width=1.2)
        c.create_text(29, 27, text="✦", fill="#9be7ff",
                      font=("Segoe UI Symbol", 13, "bold"))
        c.create_text(52, 13, text="MERLIN", fill="#92e2ff",
                      font=("Segoe UI", 9, "bold"), anchor="nw")
        c.create_text(52, 31, text="Escribe tu mensaje y pulsa Enter",
                      fill="#91a9c3", font=("Segoe UI", 8), anchor="nw")
        c.create_line(16, 62, w - 16, 62, fill="#29445f", width=1)
        c.create_text(w - 20, h - 12, text="Enter envia  ·  Esc cierra",
                      fill="#5f7a94", font=("Segoe UI", 8), anchor="e")

    def show(self) -> None:
        vx, vy, vw, _vh = _virtual_screen(self.root)
        x = vx + vw - self._W - 24
        y = vy + 72
        self.win.geometry(f"{self._W}x{self._H}+{x}+{y}")
        self.win.deiconify()
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self.win.lift()
        self.entry.delete(0, "end")
        try:
            self.win.focus_force()
        except Exception:
            pass
        self.win.after(10, self.entry.focus_set)

    def hide(self) -> None:
        self.win.withdraw()

    def _submit(self, _event=None) -> None:
        text = self.entry.get().strip()
        self.hide()
        if text and self.on_submit:
            self.on_submit(text)


class CursorOverlay:
    """Transparent overlay for MERLIN's independent agent pointer and bubbles."""

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
            0, 0, text="MERLIN", fill="#f4e7c5",
            font=("Segoe UI", 8, "bold"), anchor="n", state="hidden")

        # The bubble is drawn on the same click-through overlay so it never
        # steals focus from the application under the pointer.
        self._bubble_items = []
        self._bubble_until = 0.0
        self._bubble_text = ""
        self._status = "idle"
        self._status_detail = ""
        self._status_until = 0.0
        self._status_items = []
        self._last_status_layout = None
        self._shown = False
        self._styled = False
        self._seen_click = 0.0
        self._smooth_pos = None
        self._last_layout = None
        self._panel_items = []
        self._panel_layout = None
        self._panel_revision = 0
        self._panel_drawn_revision = -1
        self._task_title = ""
        self._task_status = "Preparando tarea"
        self._task_steps = []
        self._task_result_text = ""
        self._panel_until = 0.0

        self.root.after(_IDLE_MS, self._poll)

    # --------------------------------------------------------------- sprite art
    def _load_sprite(self):
        from PIL import Image, ImageDraw, ImageFilter, ImageTk

        img = None
        custom = _HOME / "agent_cursor.png"
        if custom.exists():
            try:
                with Image.open(custom) as src:
                    img = src.convert("RGBA")
            except Exception:
                logger.exception("could not load agent cursor art %s", custom)
        if img is None:
            # Magical arrow cursor: visually distinct from the wizard avatar.
            s = 4
            n = _SPRITE_SIZE * s
            img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            pts = [(3*s, 2*s), (3*s, 30*s), (11*s, 22*s),
                   (17*s, 36*s), (22*s, 33*s), (16*s, 20*s),
                   (28*s, 20*s)]
            d.polygon([(x+1*s, y+2*s) for x,y in pts], fill=(2, 8, 24, 235))
            d.polygon(pts, fill=(92, 222, 255, 255), outline=(245, 253, 255, 255))
            d.line(pts + [pts[0]], fill=(255, 255, 255, 255), width=1*s, joint="curve")
            d.ellipse((25*s, 2*s, 38*s, 15*s), fill=(40, 132, 255, 230), outline=(170, 244, 255, 255), width=s)
            d.line((31*s, 4*s, 31*s, 13*s), fill=(255, 255, 255, 255), width=s)
            d.line((27*s, 8*s, 35*s, 8*s), fill=(255, 255, 255, 255), width=s)
            img = img.resize((_SPRITE_SIZE, _SPRITE_SIZE), Image.LANCZOS)
        box = img.getchannel("A").getbbox()
        if box:
            img = img.crop(box)
        img.thumbnail((_SPRITE_SIZE, _SPRITE_SIZE), Image.LANCZOS)
        canvas = Image.new("RGBA", (_SPRITE_SIZE, _SPRITE_SIZE), (0, 0, 0, 0))
        canvas.paste(img, (0, 0), img)
        alpha = canvas.getchannel("A").point(lambda a: 255 if a > 80 else 0)
        canvas.putalpha(alpha)
        outline = alpha.filter(ImageFilter.MaxFilter(3))
        base = Image.new("RGBA", canvas.size, (8, 24, 48, 0))
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
    def narrate(self, text: str, duration: float = 5.0) -> None:
        """Show a polished speech bubble near the cursor; safe from worker threads."""
        text = str(text or "").strip()
        if not text:
            return
        _ui_events.put(("narrate", text, max(1.0, min(float(duration), 20.0))))
        if _task_active or _cursor_engaged:
            pulse(max(0.7, min(float(duration), 8.0)))

    def say(self, text: str, duration: float = 5.0) -> None:
        """Alias used by tools and older integrations."""
        self.narrate(text, duration=duration)

    def set_state(self, state: str, detail: str | None = None) -> None:
        """Show a compact state chip (listening/thinking/working/etc.)."""
        _ui_events.put(("state", str(state or "idle").lower(),
                        str(detail or "")))

    def _consume_ui_events(self, now: float) -> None:
        while True:
            try:
                event = _ui_events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "narrate":
                _, text, duration = event
                self._bubble_text = text
                self._bubble_until = now + duration
                self._last_layout = None
            elif event[0] == "move":
                # Position is advanced smoothly in _poll; event wakes the overlay.
                pass
            elif event[0] == "state":
                _, state, detail = event
                self._status = state
                self._status_detail = detail
                # State chips are deliberately suppressed for passive listening/wake states.
                if _task_active and state in ("thinking", "working", "executing", "browsing", "searching"):
                    self._status_until = now + 1.8
                else:
                    self._status_until = 0.0
            elif event[0] == "panel_clear":
                self._task_active_ui = False
                self._task_title = ""
                self._task_status = ""
                self._task_steps = []
                self._task_result_text = ""
                self._panel_until = 0.0
                try:
                    if getattr(self, "_result_text_widget", None) is not None:
                        self._result_text_widget.destroy()
                        self._result_text_widget = None
                except Exception:
                    pass
                self._panel_revision += 1
            elif event[0] == "task_start":
                # Backwards compatibility: task start alone must not display a panel.
                self._task_active_ui = True
                self._task_title = ""
                self._task_status = ""
                self._task_steps = []
                self._task_result_text = ""
                self._panel_until = 0.0
                self._panel_revision += 1
            elif event[0] == "task_step":
                # Legacy events intentionally ignored: operational steps are not panel content.
                pass
            elif event[0] == "task_result":
                _, result = event
                self._task_result_text = result
                self._panel_until = now + 45.0
                try:
                    if getattr(self, "_result_text_widget", None) is not None:
                        self._result_text_widget.destroy()
                        self._result_text_widget = None
                except Exception:
                    pass
                self._panel_revision += 1
            elif event[0] == "task_finish":
                self._task_active_ui = False

    def _poll(self) -> None:
        global _cursor_engaged
        try:
            now = time.monotonic()
            self._consume_ui_events(now)
            with _lock:
                active = _depth > 0
            cursor_visible = bool(_cursor_engaged and (active or now < _active_until))
            panel_visible = bool(self._task_result_text and now < self._panel_until)
            wanted = (cursor_visible or not _pings.empty() or panel_visible)
            if wanted:
                self._show()
                self.canvas.itemconfigure(self._sprite_id, state="normal" if cursor_visible else "hidden")
                self.canvas.itemconfigure(self._label_id, state="hidden")
                if cursor_visible:
                    if _INDEPENDENT:
                        pos = self._advance_agent_position(now)
                    else:
                        pos = current_position()
                    if pos:
                        px, py = pos[0] - self._vx, pos[1] - self._vy
                        if _INDEPENDENT:
                            self._smooth_pos = (float(px), float(py))
                        elif self._smooth_pos is None:
                            self._smooth_pos = (float(px), float(py))
                        else:
                            sx, sy = self._smooth_pos
                            self._smooth_pos = (sx + (px - sx) * 0.78,
                                                sy + (py - sy) * 0.78)
                        self._place(*self._smooth_pos, pointer_x=px, pointer_y=py, now=now)
                        if _click_stamp > self._seen_click:
                            self._seen_click = _click_stamp
                            self._ripple(px, py)
                else:
                    self._clear_items(self._bubble_items)
                    self._clear_items(self._status_items)
                self._draw_info_panel(now)
                self._drain_pings()
                if now >= _active_until and not active:
                    _cursor_engaged = False
            elif self._shown:
                self._hide()
        except Exception:
            logger.exception("cursor overlay tick failed")
        self.root.after(_FRAME_MS if self._shown else _IDLE_MS, self._poll)

    def _advance_agent_position(self, now: float) -> tuple[float, float] | None:
        global _agent_pos, _agent_target, _agent_move_from, _agent_move_started, _agent_move_duration
        with _agent_lock:
            if _agent_target is None and _agent_pos is None:
                native = current_position()
                if native:
                    _agent_pos = (float(native[0]), float(native[1]))
                    return _agent_pos
                return None
            if _agent_target is None:
                return _agent_pos
            start = _agent_move_from or _agent_pos or _agent_target
            duration = _agent_move_duration
            if duration <= 0:
                _agent_pos = _agent_target
            else:
                t = max(0.0, min(1.0, (now - _agent_move_started) / duration))
                eased = t * t * (3.0 - 2.0 * t)
                _agent_pos = (start[0] + (_agent_target[0] - start[0]) * eased,
                              start[1] + (_agent_target[1] - start[1]) * eased)
                if t >= 1.0:
                    _agent_pos = _agent_target
            if duration <= 0 or now - _agent_move_started >= duration:
                _agent_pos = _agent_target
                if _move_queue:
                    nx, ny, nd = _move_queue.popleft()
                    _agent_move_from = _agent_pos
                    _agent_target = (nx, ny)
                    _agent_move_started = now
                    _agent_move_duration = nd
                    if nd <= 0:
                        _agent_pos = _agent_target
            return _agent_pos

    def _show(self) -> None:
        if self._shown:
            return
        self.win.deiconify()
        self.win.update_idletasks()
        if not self._styled:  # needs a mapped window to find the real HWND
            self._make_click_through()
            self._styled = True
        self.win.lift()
        self.canvas.itemconfigure(self._sprite_id, state="hidden")
        self.canvas.itemconfigure(self._label_id, state="hidden")
        self._shown = True

    def _hide(self) -> None:
        for item in (self._sprite_id, self._label_id):
            self.canvas.itemconfigure(item, state="hidden")
        self._clear_items(self._bubble_items)
        self._clear_items(self._status_items)
        self._clear_items(self._panel_items)
        self._panel_layout = None
        try:
            if getattr(self, "_result_text_widget", None) is not None:
                self._result_text_widget.destroy()
                self._result_text_widget = None
        except Exception:
            pass
        self.win.withdraw()
        self._shown = False
        self._smooth_pos = None
        self._last_layout = None

    def _clear_items(self, items: list) -> None:
        for item in items:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        items.clear()
        if items is getattr(self, "_status_items", None):
            self._last_status_layout = None

    @staticmethod
    def _rounded_points(x1, y1, x2, y2, r=10):
        return [
            x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
            x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
            x1, y2, x1, y2-r, x1, y1+r, x1, y1
        ]

    def _place(self, x: float, y: float, pointer_x: float | None = None,
               pointer_y: float | None = None, now: float | None = None) -> None:
        sx, sy = x + _OFFSET[0], y + _OFFSET[1]
        self.canvas.coords(self._sprite_id, sx, sy)
        self.canvas.coords(self._label_id, sx + _SPRITE_SIZE / 2,
                           sy + _SPRITE_SIZE + 1)
        now = now or time.monotonic()
        px = x if pointer_x is None else pointer_x
        py = y if pointer_y is None else pointer_y
        if now < self._bubble_until:
            self._draw_bubble(px, py, sx, sy)
        else:
            self._clear_items(self._bubble_items)
            self._last_layout = None
        if now < self._status_until:
            self._draw_status(sx, sy, now)
        else:
            self._clear_items(self._status_items)

    def _draw_bubble(self, px: float, py: float, sx: float, sy: float) -> None:
        """A compact glassy bubble that stays inside the virtual desktop."""
        import tkinter.font as tkfont

        text = self._bubble_text
        if not text:
            self._clear_items(self._bubble_items)
            return

        max_chars = 43
        lines = []
        for paragraph in text.splitlines() or [text]:
            lines.extend(textwrap.wrap(paragraph, width=max_chars) or [""])
        if len(lines) > 4:
            lines = lines[:4]
            if lines[-1]:
                lines[-1] = lines[-1][:max_chars - 1].rstrip() + "…"
        shown = "\n".join(lines)
        font = tkfont.Font(family="Segoe UI", size=10)
        text_w = min(292, max(142, max(font.measure(line) for line in lines) + 4))
        text_h = max(1, len(lines)) * 18
        w, h = text_w + 34, text_h + 44

        # Prefer above/right of the pointer, flip when close to a screen edge.
        x1 = sx + _SPRITE_SIZE + 8
        y1 = sy - h - 10
        if x1 + w > self._vw - 8:
            x1 = sx - w - 10
        if x1 < 8:
            x1 = max(8, min(px + 18, self._vw - w - 8))
        if y1 < 8:
            y1 = sy + _SPRITE_SIZE + 12
        if y1 + h > self._vh - 8:
            y1 = max(8, self._vh - h - 8)

        layout = (round(x1), round(y1), w, h, shown)
        if self._last_layout == layout and self._bubble_items:
            return
        self._clear_items(self._bubble_items)
        # shadow, rounded body, small pointer tail, title, text
        shadow = self.canvas.create_polygon(
            self._rounded_points(x1+4, y1+5, x1+w+4, y1+h+5, 15),
            smooth=True, fill="#020713", outline="")
        body = self.canvas.create_polygon(
            self._rounded_points(x1, y1, x1+w, y1+h, 15),
            smooth=True, fill="#0d1b30", outline="#58caff", width=1.5)
        accent = self.canvas.create_line(
            x1+15, y1+8, x1+w-15, y1+8, fill="#347fbd", width=2)
        tail_x = min(max(sx + _SPRITE_SIZE / 2, x1 + 20), x1 + w - 20)
        tail_y = y1 + h if y1 < sy else y1
        tail_dir = 1 if y1 < sy else -1
        tail = self.canvas.create_polygon(
            tail_x-8, tail_y, tail_x+8, tail_y,
            tail_x, tail_y+tail_dir*10,
            fill="#0d1b30", outline="#58caff", width=1)
        title = self.canvas.create_text(
            x1+15, y1+13, text="✦  MERLIN  ·  EN ACCIÓN", fill="#83dcff",
            font=("Segoe UI", 8, "bold"), anchor="nw")
        message = self.canvas.create_text(
            x1+15, y1+31, text=shown, fill="#f0f6ff",
            font=("Segoe UI", 10), anchor="nw", width=w-30, justify="left")
        self._bubble_items.extend((shadow, body, accent, tail, title, message))
        self._last_layout = layout

    def _draw_status(self, sx: float, sy: float, now: float) -> None:
        import tkinter.font as tkfont

        detail = self._status_detail.strip()
        label = detail if detail and len(detail) <= 30 else _STATE_LABELS.get(
            self._status, self._status.replace("_", " ").title())
        if len(label) > 32:
            label = label[:31] + "…"
        font = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        w = max(72, min(210, font.measure(label) + 30))
        h = 24
        x1 = sx + (_SPRITE_SIZE - w) / 2
        y1 = sy + _SPRITE_SIZE + 7
        if y1 + h > self._vh - 5:
            y1 = max(5, sy - h - 5)
        layout = (round(x1), round(y1), w, h, label, self._status)
        if getattr(self, "_last_status_layout", None) == layout and self._status_items:
            return
        self._clear_items(self._status_items)
        color = "#f0b84c" if self._status in ("thinking", "working", "executing") else "#55c8ff"
        shadow = self.canvas.create_polygon(
            self._rounded_points(x1+2, y1+2, x1+w+2, y1+h+2, 10),
            smooth=True, fill="#020713", outline="")
        pill = self.canvas.create_polygon(
            self._rounded_points(x1, y1, x1+w, y1+h, 10),
            smooth=True, fill="#101f38", outline=color, width=1.2)
        dot = self.canvas.create_oval(x1+8, y1+9, x1+14, y1+15,
                                      fill=color, outline="")
        text_id = self.canvas.create_text(
            x1+20, y1+h/2, text=label, fill="#edf7ff",
            font=("Segoe UI", 8, "bold"), anchor="w")
        self._status_items.extend((shadow, pill, dot, text_id))
        self._last_status_layout = layout

    def _draw_info_panel(self, now: float) -> None:
        """Show only the final answer/research result in the right-side panel."""
        result = str(self._task_result_text or "").strip()
        if now >= self._panel_until or not result:
            if self._panel_items:
                self._clear_items(self._panel_items)
            try:
                if getattr(self, "_result_text_widget", None) is not None:
                    self._result_text_widget.destroy()
                    self._result_text_widget = None
            except Exception:
                pass
            self._panel_layout = None
            return
        if self._panel_drawn_revision == self._panel_revision and self._panel_items:
            return
        self._clear_items(self._panel_items)

        import tkinter.font as tkfont
        w = min(560, max(400, int(self._vw * 0.33)))
        x1 = max(10, self._vw - w - 24)
        y1 = 72
        # This card is for final answers and research findings only.
        body_font = tkfont.Font(family="Segoe UI", size=11)
        max_chars = 2100
        body = result[:max_chars].strip()
        if len(result) > max_chars:
            body = body.rstrip() + "…"
        wrap_width = w - 42
        # Estimate height from wrapped visual lines; keep generous spacing.
        lines = 0
        for para in body.splitlines() or [body]:
            lines += max(1, len(textwrap.wrap(para, width=58)))
        body_h = min(620, max(110, lines * 19 + 16))
        h = min(760, max(230, 88 + body_h + 22))
        x2, y2 = x1 + w, y1 + h
        items = []
        items.append(self.canvas.create_polygon(
            self._rounded_points(x1+5, y1+6, x2+5, y2+6, 19),
            smooth=True, fill="#020713", outline=""))
        items.append(self.canvas.create_polygon(
            self._rounded_points(x1, y1, x2, y2, 19), smooth=True,
            fill="#0a1425", outline="#397fae", width=1.5))
        items.append(self.canvas.create_polygon(
            self._rounded_points(x1+1, y1+1, x2-1, y1+57, 17),
            smooth=True, fill="#112640", outline=""))
        items.append(self.canvas.create_oval(
            x1+16, y1+15, x1+42, y1+41,
            fill="#173c60", outline="#59c9ff", width=1.2))
        items.append(self.canvas.create_text(
            x1+29, y1+28, text="✦", fill="#9be7ff",
            font=("Segoe UI Symbol", 13, "bold"), anchor="center"))
        items.append(self.canvas.create_text(
            x1+52, y1+13, text="RESULTADOS", fill="#92e2ff",
            font=("Segoe UI", 9, "bold"), anchor="nw"))
        items.append(self.canvas.create_text(
            x1+52, y1+31, text="Respuesta e investigación", fill="#91a9c3",
            font=("Segoe UI", 8), anchor="nw"))
        items.append(self.canvas.create_line(x1+16, y1+62, x2-16, y1+62,
                                             fill="#29445f", width=1))
        # Text widget provides natural wrapping and a scrollbar for long research answers.
        # It is placed on a separate native child window and does not intercept clicks
        # outside the result card.
        try:
            import tkinter as tk
            if getattr(self, "_result_text_widget", None) is not None:
                self._result_text_widget.destroy()
            if getattr(self, "_result_scrollbar", None) is not None:
                self._result_scrollbar.destroy()
            text_h = max(6, min(26, int((h - 94) / 19)))
            self._result_text_widget = tk.Text(
                self.win, wrap="word", height=text_h, width=max(32, int((w-42)/8)),
                bg="#0a1425", fg="#e3edf9", insertbackground="#e3edf9",
                relief="flat", borderwidth=0, highlightthickness=0,
                font=("Segoe UI", 11), padx=3, pady=4,
                spacing1=3, spacing3=4, selectbackground="#244c70")
            self._result_text_widget.insert("1.0", body)
            self._result_text_widget.configure(state="disabled")
            self._result_text_widget.place(x=x1+19, y=y1+72, width=w-38, height=h-88)
            # The overlay window is click-through by design; the panel is informational.
            self._panel_items.extend(items)
            self._panel_layout = (x1, y1, w, h, body)
            self._panel_drawn_revision = self._panel_revision
            return
        except Exception:
            logger.debug("Could not create result text widget; falling back to canvas", exc_info=True)

        # Fallback for environments where embedding a Text widget is unavailable.
        items.append(self.canvas.create_text(
            x1+19, y1+74, text=body, fill="#e3edf9", font=("Segoe UI", 11),
            anchor="nw", width=wrap_width, justify="left"))
        self._panel_items.extend(items)
        self._panel_layout = (x1, y1, w, h, body)
        self._panel_drawn_revision = self._panel_revision

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