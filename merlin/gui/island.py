"""MERLIN's control island: translucent panel pinned to the TOP-CENTRE.

Pure Tk widgets (no CustomTkinter): on high-DPI Windows displays CTk widgets
inside a plain tk.Toplevel self-scale (x1.5) while the canvas does not, which
pushed the entry below the panel. Tk-only keeps one single coordinate space.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("merlin.gui.island")

_PILL_BG = "#141923"
_PILL_ALPHA = 0.93
_BORDER = "#33405e"
_BADGE_BG = "#1b2a4a"
_CYAN = "#22d3ee"
_STATUS_FG = "#dfe6f0"
_ENTRY_BG, _ENTRY_BORDER = "#0e121b", "#2c3550"
_ENTRY_FG, _PLACEHOLDER_FG = "#e6ecf5", "#6b7488"
_STATE_TEXT = {"idle": "Waiting for wake word", "listening": "Listening",
               "thinking": "Thinking", "speaking": "Speaking"}
_DETAIL_TEXT = {
    "waiting for wake word": "Waiting for wake word",
    "listening (session)": "Listening",
    "recording": "Listening",
    "transcribing": "Transcribing",
    "router": "Thinking",
    "direct_response": "Thinking",
    "os_agent": "Working on your system",
    "web_agent": "Researching the web",
    "opencode_agent": "Coding",
    "tts_responder": "Speaking",
}
_STATES = set(_STATE_TEXT)

_W, _H = 430, 104
_RADIUS = 18
_Y = 16
_SLIDE_FROM = -(_H + 8)
_FADE_STEPS = 8
_PLACEHOLDER = "Type your request..."


class DynamicIsland:
    def __init__(self, app) -> None:
        import tkinter as tk

        self.app = app
        self.state = "idle"
        self._visible = False
        self._last_detail = None
        self._resp_job = None
        self._placeholder = True
        try:
            self._x = (app.winfo_screenwidth() - _W) // 2
        except Exception:
            self._x = (1920 - _W) // 2

        self.win = tk.Toplevel(app)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", "black")
        self.win.attributes("-alpha", 0.0)
        self.win.configure(bg="black")

        self.canvas = tk.Canvas(self.win, width=_W, height=_H, bg="black",
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        # --- panel + spiral badge + status (created ONCE) --------------------
        self._panel = self.canvas.create_polygon(
            self._panel_pts(), smooth=True, fill=_PILL_BG, outline=_BORDER,
            width=1)
        self.canvas.create_oval(15, 11, 45, 41, fill=_BADGE_BG,
                                outline="#33405e", width=2)
        self.canvas.create_arc(19, 15, 41, 37, start=200, extent=140,
                               style="arc", outline=_CYAN, width=2)
        self.canvas.create_arc(23, 19, 37, 33, start=20, extent=140,
                               style="arc", outline="#8fe9ff", width=2)
        self._status = self.canvas.create_text(
            58, 26, anchor="w", text="MERLIN - Waiting for wake word",
            fill=_STATUS_FG, font=("Segoe UI", 14, "bold"))

        # --- row 1: voice-mode switch (plain Tk Checkbutton) -----------------
        self.voice_switch = tk.Checkbutton(
            self.win, text="Voice mode", variable=app.voice_mode,
            command=app._on_voice_toggle,
            bg=_PILL_BG, fg="#cfd3da", activebackground=_PILL_BG,
            activeforeground="#cfd3da", selectcolor=_ENTRY_BG,
            bd=0, highlightthickness=0, font=("Segoe UI", 10),
            cursor="hand2")
        self.voice_switch.place(x=_W - 150, y=12)
        app.voice_mode.trace_add("write", lambda *_: self.refresh())

        # --- row 2: text entry + mic (plain Tk, exact pixels) ----------------
        self.entry = tk.Entry(
            self.win, width=44, bg=_ENTRY_BG, fg=_ENTRY_FG,
            insertbackground=_ENTRY_FG, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=_ENTRY_BORDER,
            highlightcolor=_ENTRY_BORDER, font=("Segoe UI", 11))
        self.entry.place(x=16, y=60, height=32)
        self.entry.insert(0, _PLACEHOLDER)
        self.entry.configure(fg=_PLACEHOLDER_FG)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Return>", lambda _e: self._on_send())

        self.mic_btn = tk.Button(
            self.win, text="🎙", bg="#1d2a4a", activebackground="#2d4170",
            fg=_ENTRY_FG, relief="flat", bd=0, highlightthickness=0,
            cursor="hand2", font=("Segoe UI", 12),
            command=app.push_to_talk_async)
        self.mic_btn.place(x=_W - 52, y=60, width=36, height=32)

        self._show()

    # ---------------------------------------------------------- placeholder
    def _focus_in(self, _e) -> None:
        if self._placeholder:
            self.entry.delete(0, "end")
            self.entry.configure(fg=_ENTRY_FG)
            self._placeholder = False

    def _focus_out(self, _e) -> None:
        if not self.entry.get():
            self.entry.insert(0, _PLACEHOLDER)
            self.entry.configure(fg=_PLACEHOLDER_FG)
            self._placeholder = True

    # ------------------------------------------------------------- panel art
    @staticmethod
    def _panel_pts() -> list:
        r, x0, y0, x1, y1 = _RADIUS, 2, 2, _W - 2, _H - 2
        return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
                x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]

    # ------------------------------------------------------------- placement
    def _position(self, y: int) -> None:
        self.win.geometry(f"{_W}x{_H}+{self._x}+{y}")

    # ------------------------------------------------------------- animation
    def _fade(self, alpha_from: float, alpha_to: float, y_from: int,
              y_to: int, done=None) -> None:
        def step(i: int = 1) -> None:
            f = i / _FADE_STEPS
            self.win.attributes("-alpha", min(1.0, max(
                0.0, alpha_from + (alpha_to - alpha_from) * f)))
            self._position(int(y_from + (y_to - y_from) * f))
            if i < _FADE_STEPS:
                self.win.after(22, step, i + 1)
            elif done is not None:
                done()
        step()

    def _show(self) -> None:
        if self._visible:
            return
        self._visible = True
        self._position(_SLIDE_FROM)
        self.win.deiconify()
        self._fade(0.0, _PILL_ALPHA, _SLIDE_FROM, _Y)

    # ------------------------------------------------------------- behaviour
    def reveal(self, input_focus: bool = False) -> None:
        self._show()
        if input_focus:
            self.win.after(120, self._focus_entry)

    def _focus_entry(self) -> None:
        try:
            self.win.lift()
            self.win.focus_force()
            self._focus_in(None)          # clear placeholder so typing is clean
            self.entry.focus_force()
        except Exception:
            logger.exception("could not focus the island entry")

    def set_state(self, state: str, detail: str = None) -> None:
        if state not in _STATES:
            return
        self.state = state
        self._last_detail = detail
        self.refresh()

    def refresh(self) -> None:
        detail = _DETAIL_TEXT.get(str(self._last_detail or "").lower().strip(),
                                  _STATE_TEXT[self.state])
        if self.app.voice_mode.get():
            text = "VOICE ACTIVE - MERLIN"
        else:
            text = f"MERLIN - {detail}"
        self.canvas.itemconfigure(self._status, text=text)

    def show_response(self, text: str) -> None:
        """Show MERLIN's answer briefly in the status line: visible feedback for
        text input (the wizard itself carries no text)."""
        if not text:
            return
        short = text if len(text) <= 90 else text[:87] + "..."
        self.canvas.itemconfigure(self._status, text=short)
        if self._resp_job is not None:
            try:
                self.win.after_cancel(self._resp_job)
            except Exception:
                pass
        self._resp_job = self.win.after(6000, self.refresh)

    # ------------------------------------------------------------- callbacks
    def _on_send(self, event=None) -> None:
        """Maneja el envío del texto desde la Dynamic Island."""
        if self._placeholder:
            logger.debug("Ignorado: el entry tiene el placeholder activo")
            return
            
        query = self.entry.get().strip()
        if not query:
            logger.debug("Ignorado: el entry está vacío")
            return
            
        logger.info("📝 Island send triggered with query: '%s'", query)
        
        self.entry.delete(0, "end")
        self._focus_out(None)  
        
        submit_method = (
            getattr(self.app, "widget_submit", None) or 
            getattr(self.app, "submit_query", None) or 
            getattr(self.app, "process_text_input", None)
        )
            
        if submit_method:
            logger.info("✅ Llamando a: %s", submit_method.__name__)
            submit_method(query)
        else:
            logger.error("❌ No se encontró un método de submit en la app.")
            logger.error("Métodos disponibles en app: %s", 
                         [m for m in dir(self.app) if not m.startswith("_") and ("submit" in m.lower() or "query" in m.lower() or "input" in m.lower())])