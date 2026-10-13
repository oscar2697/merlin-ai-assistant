"""MERLIN's floating desktop widget: pure-vector wizard PEEKING from behind
the taskbar (design-mock proportions). Hair is drawn BEHIND the face so the
smooth tufts frame it instead of covering it. Crop falls just below the eyes.
Scene graph created ONCE; the ~30 fps loop only calls coords()/itemconfigure().
"""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

logger = logging.getLogger("merlin.gui.widget")

# ----------------------------------------------------------------- palette
_KEY = "#010101"
_HAT, _HAT_D = "#2f6fc4", "#1d4b93"
_SKIN = "#f2d6ac"
_HAIR, _HAIR_D = "#dbe2ea", "#b9c4cf"
_BROW = "#3f4a5c"
_IRIS, _PUPIL = "#2f7fd6", "#101a2e"
_BAND = "#8a5a2b"
_SPARK = "#8fe9ff"

_STATE = {"idle": "#2f8cff", "listening": "#00D2FF",
          "thinking": "#F59E0B", "speaking": "#7FD48A"}

_NARRATION_MAP = {
    "os_agent": "Ejecutando tarea...", "web_agent": "Researching...",
    "opencode_agent": "Preparando el prompt...", "direct_response": "Pensando...",
    "voice_listener": "Escuchando...", "tts_responder": "Hablando...",
    "recording": "Escuchando...", "transcribing": "Transcribiendo...",
    "listening (session)": "Escuchando...", "thinking": "Pensando...",
}
_NARRATION_MS = 4000

# ----------------------------------------------------------------- geometry
_W, _H = 380, 330
_AX = _W // 2
_FRAME_MS = 33
_POS_FILE = Path.home() / "merlin" / "widget_pos.json"
# Chin baseline BELOW the window bottom edge. Crop falls just under the eyes
# (mock). The rise on wake is subtle: 8-16 px, never shows a full face.
_PEEK = {"idle": 124.0, "listening": 108.0, "thinking": 116.0, "speaking": 110.0}


def _rounded_rect(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


class MerlinWidget:
    def __init__(self, app) -> None:
        import tkinter as tk

        self.app = app
        self.state = "idle"
        self._phase = 0.0
        self._n = 0
        self._blink = False
        self._drag = None
        self._wake_ticks = 0
        self._peek = _PEEK["idle"]
        self._narration = {"text": "", "until": 0.0}

        self.win = tk.Toplevel(app)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", _KEY)
        self.win.configure(bg=_KEY)
        self._x, self._y = self._load_pos()

        self.canvas = tk.Canvas(self.win, bg=_KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._context_menu)

        self._build_scene()
        self.win.geometry(f"{_W}x{_H}+{self._x}+{self._y}")
        self.canvas.configure(width=_W, height=_H)
        self.win.deiconify()
        self._tick()

    # ------------------------------------------------------------- geometry
    def _work_area(self):
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            rc = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0):
                if rc.right > rc.left and rc.bottom > rc.top:
                    return rc.left, rc.top, rc.right, rc.bottom
        except Exception:
            pass
        return 0, 0, sw, sh

    def _load_pos(self):
        wa_l, wa_t, wa_r, wa_b = self._work_area()
        default_x = wa_l + (wa_r - wa_l - _W) // 2
        default_y = wa_b - _H
        try:
            data = json.loads(_POS_FILE.read_text(encoding="utf-8"))
            x, y = int(data["x"]), int(data["y"])
        except Exception:
            return default_x, default_y
        x = min(max(x, wa_l + 12), wa_r - _W - 12)
        y = min(max(y, wa_t + 12), wa_b - _H)
        return x, y

    def _save_pos(self) -> None:
        try:
            _POS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _POS_FILE.write_text(json.dumps({"x": self._x, "y": self._y}),
                                 encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------ scene
    def _build_scene(self) -> None:
        c = self.canvas
        # Z-ORDER: hair BEHIND the face (smooth tufts must not cover it).
        self._bust = {
            "hair_l": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_HAIR,
                                       outline=_HAIR_D, smooth=True),
            "hair_r": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_HAIR,
                                       outline=_HAIR_D, smooth=True),
            "face": c.create_oval(0, 0, 0, 0, fill=_SKIN, outline="#c9a978"),
            "brow_l": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_BROW, outline="",
                                       smooth=True),
            "brow_r": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_BROW, outline="",
                                       smooth=True),
            "eye_wl": c.create_oval(0, 0, 0, 0, fill="#ffffff",
                                    outline="#3d4a5c", width=2),
            "eye_wr": c.create_oval(0, 0, 0, 0, fill="#ffffff",
                                    outline="#3d4a5c", width=2),
            "iris_l": c.create_oval(0, 0, 0, 0, fill=_IRIS, outline="#1d5fa8"),
            "iris_r": c.create_oval(0, 0, 0, 0, fill=_IRIS, outline="#1d5fa8"),
            "pup_l": c.create_oval(0, 0, 0, 0, fill=_PUPIL, outline=""),
            "pup_r": c.create_oval(0, 0, 0, 0, fill=_PUPIL, outline=""),
            "hi_l": c.create_oval(0, 0, 0, 0, fill="#ffffff", outline=""),
            "hi_r": c.create_oval(0, 0, 0, 0, fill="#ffffff", outline=""),
            "eye_cl": c.create_arc(0, 0, 0, 0, start=200, extent=140, style="arc",
                                   outline="#3d4a5c", width=3),
            "eye_cr": c.create_arc(0, 0, 0, 0, start=200, extent=140, style="arc",
                                   outline="#3d4a5c", width=3),
            "cone": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_HAT, outline=_HAT_D,
                                     width=2, smooth=True),
            "cone_sh": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_HAT_D, outline="",
                                        smooth=True),
            "band": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_BAND, outline="",
                                     smooth=True),
            "brim": c.create_polygon(0, 0, 0, 0, 0, 0, fill=_HAT, outline=_HAT_D,
                                     width=2, smooth=True),
            "spark": [c.create_oval(0, 0, 0, 0, fill=_SPARK, outline="")
                      for _ in range(3)],
            "zzz": [c.create_text(0, 0, text=t, fill="#8a93a8",
                                  font=("Segoe UI", sz, "bold"))
                    for t, sz in (("z", 10), ("Z", 13), ("Z", 16))],
        }
        self._flash = c.create_oval(0, 0, 0, 0, fill="", outline="",
                                    state="hidden")
        self._narr = {
            "poly": c.create_polygon(_rounded_rect(8, 6, 208, 36, 12),
                                     smooth=True, fill="#0f2238",
                                     outline="#2f9bff", width=2, state="hidden"),
            "text": c.create_text(_AX, 21, text="", fill="#d6ecff",
                                  font=("Segoe UI", 10), state="hidden"),
            "d1": c.create_oval(0, 0, 0, 0, fill="#0f2238", outline="#2f9bff",
                                state="hidden"),
            "d2": c.create_oval(0, 0, 0, 0, fill="#0f2238", outline="#2f9bff",
                                state="hidden"),
        }
        self._layout_bust(0.0, 0.0, 0.0)

    def _layout_bust(self, dx: float, dy: float, bob: float) -> None:
        c, b = self.canvas, self._bust
        ax, ay = _AX, _H + self._peek + bob

        # side hair BEHIND the face: only the outer tufts show (mock look)
        c.coords(b["hair_l"], ax - 50, ay - 188, ax - 74, ay - 168,
                 ax - 86, ay - 130, ax - 78, ay - 92, ax - 58, ay - 72,
                 ax - 44, ay - 90, ax - 46, ay - 150)
        c.coords(b["hair_r"], ax + 50, ay - 188, ax + 74, ay - 168,
                 ax + 86, ay - 130, ax + 78, ay - 92, ax + 58, ay - 72,
                 ax + 44, ay - 90, ax + 46, ay - 150)
        c.coords(b["face"], ax - 62, ay - 196, ax + 62, ay - 64)
        if self.state == "listening":
            o = (-6,) * 6
        elif self.state == "thinking":
            o = (0, 2, 5, 5, 2, 0)
        else:
            o = (0,) * 6
        c.coords(b["brow_l"], ax - 40, ay - 186 + o[0], ax - 24, ay - 192 + o[1],
                 ax - 10, ay - 186 + o[2], ax - 12, ay - 178 + o[3],
                 ax - 24, ay - 183 + o[4], ax - 38, ay - 178 + o[5])
        c.coords(b["brow_r"], ax + 40, ay - 186 + o[0], ax + 24, ay - 192 + o[1],
                 ax + 10, ay - 186 + o[2], ax + 12, ay - 178 + o[3],
                 ax + 24, ay - 183 + o[4], ax + 38, ay - 178 + o[5])
        c.coords(b["eye_wl"], ax - 38, ay - 168, ax - 4, ay - 132)
        c.coords(b["eye_wr"], ax + 4, ay - 168, ax + 38, ay - 132)
        c.coords(b["iris_l"], ax - 30 + dx, ay - 160 + dy, ax - 12 + dx, ay - 142 + dy)
        c.coords(b["iris_r"], ax + 12 + dx, ay - 160 + dy, ax + 30 + dx, ay - 142 + dy)
        c.coords(b["pup_l"], ax - 25.5 + dx, ay - 155.5 + dy,
                 ax - 16.5 + dx, ay - 146.5 + dy)
        c.coords(b["pup_r"], ax + 16.5 + dx, ay - 155.5 + dy,
                 ax + 25.5 + dx, ay - 146.5 + dy)
        c.coords(b["hi_l"], ax - 19 + dx, ay - 158 + dy, ax - 14 + dx, ay - 153 + dy)
        c.coords(b["hi_r"], ax + 14 + dx, ay - 158 + dy, ax + 19 + dx, ay - 153 + dy)
        c.coords(b["eye_cl"], ax - 38, ay - 168, ax - 4, ay - 132)
        c.coords(b["eye_cr"], ax + 4, ay - 168, ax + 38, ay - 132)
        c.coords(b["cone"], ax - 46, ay - 220, ax - 42, ay - 268, ax - 24, ay - 306,
                 ax + 4, ay - 322, ax + 36, ay - 320, ax + 68, ay - 306,
                 ax + 88, ay - 286, ax + 74, ay - 278, ax + 50, ay - 276,
                 ax + 38, ay - 248, ax + 46, ay - 220)
        c.coords(b["cone_sh"], ax + 10, ay - 220, ax + 20, ay - 262, ax + 42, ay - 288,
                 ax + 86, ay - 284, ax + 74, ay - 278, ax + 50, ay - 276,
                 ax + 38, ay - 248, ax + 46, ay - 220)
        c.coords(b["band"], ax - 48, ay - 220, ax + 48, ay - 220,
                 ax + 46, ay - 240, ax - 46, ay - 240)
        c.coords(b["brim"], ax - 108, ay - 190, ax - 70, ay - 212, ax, ay - 222,
                 ax + 70, ay - 212, ax + 108, ay - 190, ax + 66, ay - 202,
                 ax, ay - 212, ax - 66, ay - 202)
        for i, (sx, sy, r) in enumerate(((-86, -292, 3.0), (94, -262, 2.5),
                                         (64, -140, 2.2))):
            tw = r * (1.3 + 0.8 * math.sin(self._phase * 3 + i * 2))
            c.coords(b["spark"][i], ax + sx - tw, ay + sy - tw,
                     ax + sx + tw, ay + sy + tw)
        for i, zz in enumerate(b["zzz"]):
            drift = ((self._phase + i) % 6) / 6.0
            c.coords(zz, ax + 56 + i * 14, ay - 160 - drift * 22)

    def _tick(self) -> None:
        try:
            self._phase += 0.08
            self._n += 1
            self._blink = (self._n % 150) < 4
            tgt = _PEEK.get(self.state, 120.0)
            self._peek += (tgt - self._peek) * 0.12
            self._update_scene()
        except Exception:
            logger.exception("widget tick failed")
        finally:
            self.win.after(_FRAME_MS, self._tick)

    def _update_scene(self) -> None:
        c, b = self.canvas, self._bust
        dx, dy = self._gaze()
        self._layout_bust(dx, dy, math.sin(self._phase * 1.3) * 2.0)

        open_eyes = not (self.state == "idle" or self._blink)
        for k in ("eye_wl", "eye_wr", "iris_l", "iris_r", "pup_l", "pup_r",
                  "hi_l", "hi_r"):
            c.itemconfigure(b[k], state="normal" if open_eyes else "hidden")
        for k in ("eye_cl", "eye_cr"):
            c.itemconfigure(b[k], state="hidden" if open_eyes else "normal")
        spark = _SPARK if self.state != "idle" else "#3a4a63"
        for sp in b["spark"]:
            c.itemconfigure(sp, fill=spark)
        zzz = "normal" if self.state == "idle" else "hidden"
        for zz in b["zzz"]:
            c.itemconfigure(zz, state=zzz)

        if self._wake_ticks > 0:
            ax, ay = _AX, _H + self._peek
            k = (24 - self._wake_ticks) * 4
            c.coords(self._flash, ax - 110 - k, ay - 330 - k,
                     ax + 110 + k, ay - 60 + k)
            c.itemconfigure(self._flash, outline="#00D2FF",
                            width=max(1, self._wake_ticks // 6), state="normal")
            self._wake_ticks -= 1
        else:
            c.itemconfigure(self._flash, state="hidden")

        self._update_narration(c)

    def _update_narration(self, c) -> None:
        text = self._narration["text"]
        items = (self._narr["poly"], self._narr["text"],
                 self._narr["d1"], self._narr["d2"])
        if not text or time.time() >= self._narration["until"]:
            for it in items:
                c.itemconfigure(it, state="hidden")
            return
        w = max(120, 8 * len(text) + 28)
        x1, y1, h, r = _AX - w / 2, 6, 30, 12
        x2, y2 = x1 + w, y1 + h
        c.coords(self._narr["poly"], *_rounded_rect(x1, y1, x2, y2, r))
        c.coords(self._narr["text"], (x1 + x2) / 2, (y1 + y2) / 2)
        c.itemconfigure(self._narr["text"], text=text)
        for i, (bx, by, rr) in enumerate(((x2 - 18, y2 + 7, 4),
                                          (x2 - 8, y2 + 16, 2.5))):
            dot = self._narr["d1"] if i == 0 else self._narr["d2"]
            c.coords(dot, bx - rr, by - rr, bx + rr, by + rr)
        for it in items:
            c.itemconfigure(it, state="normal")

    def _gaze(self):
        try:
            import ctypes
            from ctypes import wintypes
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            gx = (pt.x - (self.win.winfo_rootx() + _AX)) / 1600
            gy = (pt.y - (self.win.winfo_rooty() + _H)) / 1000
            return max(-4.0, min(4.0, gx * 20)), max(-3.0, min(3.0, gy * 14))
        except Exception:
            return (1.5, -2.0) if self.state == "thinking" else (0.0, 0.0)

    # ---------------------------------------------------------- interactions
    def _context_menu(self, event) -> None:
        import tkinter as tk
        menu = tk.Menu(self.win, tearoff=0, bg="#17150f", fg="#f2e6c9",
                       activebackground="#e0a83c", activeforeground="#17150f",
                       borderwidth=0)
        menu.add_command(label="🎤  Push-to-talk",
                         command=self.app.push_to_talk_async)
        menu.add_command(label="⌨  Escribir a MERLIN", command=self.summon_island)
        menu.add_command(label="💬  Ver chat completo",
                         command=self.app.toggle_dashboard)
        menu.add_separator()
        menu.add_command(label="🚪  Salir de MERLIN", command=self.app.quit_app)
        menu.tk_popup(event.x_root, event.y_root)

    def summon_island(self) -> None:
        island = getattr(self.app, "island", None)
        if island:
            island.reveal(input_focus=True)

    def _press(self, event) -> None:
        self._drag = {"x0": event.x_root, "y0": event.y_root,
                      "wx": self._x, "wy": self._y, "moved": False}

    def _drag_move(self, event) -> None:
        if not self._drag:
            return
        dx = event.x_root - self._drag["x0"]
        dy = event.y_root - self._drag["y0"]
        if abs(dx) + abs(dy) > 6:
            self._drag["moved"] = True
            self._x = self._drag["wx"] + dx
            self._y = self._drag["wy"] + dy
            self.win.geometry(f"+{self._x}+{self._y}")

    def _release(self, _event) -> None:
        if not self._drag:
            return
        if self._drag["moved"]:
            self._save_pos()
        else:
            self.summon_island()
        self._drag = None

    # ------------------------------------------------------------ public API
    def set_state(self, state: str, detail: str = None) -> None:
        previous = self.state
        if state in _STATE:
            self.state = state
        if detail:
            narration = _NARRATION_MAP.get(str(detail).lower())
            if narration:
                self.narrate(narration)
        if previous == "idle" and state != "idle":
            self._wake_ticks = 24

    def narrate(self, text: str) -> None:
        if text:
            self._narration = {"text": str(text),
                               "until": time.time() + _NARRATION_MS / 1000}

    def say(self, text: str) -> None:
        """Compat: la respuesta va por voz; el widget solo refleja el estado."""