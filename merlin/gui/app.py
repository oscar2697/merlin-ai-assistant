"""CustomTkinter GUI: dark medieval-style chat BUBBLES + live node status + mic.

The graph runs in background threads (non-blocking UI per CONTEXT.md) and
communicates through the merlin.events queue.

Voice input lives here, outside the graph: the mic button is push-to-talk and
the "Voice mode" switch runs a hands-free wake-word loop in its own thread.
Both only touch the runtime once they have final text to submit, so the
runtime's busy lock is never held while waiting for the microphone.

MERLIN's visible cursor overlay (cursor_overlay.py) is attached here.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import tkinter

import customtkinter as ctk

from merlin.config import get_config
from merlin.events import drain_events, post_event
from merlin.gui import cursor_overlay
from merlin.tools import voice_tools

logger = logging.getLogger("merlin.gui")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Bubble colours (medieval palette)
_USER_BG = "#1f3a5f"       # deep blue
_MERLIN_BG = "#2b2415"     # dark gold tint
_MERLIN_FG = "#e8d5a3"     # parchment
_SYSTEM_FG = "#8a8f98"
_ERROR_FG = "#ff6b6b"
_WRAP = 580


def _state_for(status: str) -> str:
    """Raw status event -> avatar/island state (idle, listening, thinking, speaking).

    'waiting for wake word' is IDLE (the wizard is just waiting); LISTENING means
    the microphone is actually capturing or transcribing; any graph node working
    (os_agent, web_agent, router...) is THINKING; the TTS node is SPEAKING."""
    s = str(status or "").lower().strip()
    if not s or s == "idle" or "wake word" in s:
        return "idle"
    if any(k in s for k in ("recording", "listening", "transcribing")):
        return "listening"
    if "tts" in s or "speak" in s:
        return "speaking"
    if any(k in s for k in ("thinking", "router", "agent", "direct_response",
                            "self_improvement")):
        return "thinking"
    return "idle"


class _Bubble(ctk.CTkFrame):
    """One dialogue bubble: rounded frame + wrapped text."""

    def __init__(self, master, text: str, *, who: str = "merlin",
                 anchor: str = "w", fg: str = None, bg: str = None,
                 font_size: int = 14) -> None:
        colors = {
            "user": (_USER_BG, "#e6ecf5"),
            "merlin": (_MERLIN_BG, _MERLIN_FG),
        }.get(who, (None, fg or _SYSTEM_FG))
        super().__init__(master, corner_radius=16,
                         fg_color=(bg or colors[0] or "transparent"))
        self.label = ctk.CTkLabel(
            self, text=text, wraplength=_WRAP, justify="left",
            text_color=colors[1], font=ctk.CTkFont(size=font_size))
        self.label.pack(padx=12, pady=8)
        self.pack(anchor=anchor, padx=10, pady=4, fill="x", expand=False)

    def set_text(self, text: str) -> None:
        self.label.configure(text=text)


class MerlinApp(ctk.CTk):
    POLL_MS = 120

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.voice_mode = tkinter.BooleanVar(value=False)
        self._stream_bubble = None   # streaming MERLIN bubble (being filled)
        self._last_heard = ""       # last voice transcription (session logic)
        self._wake_stop = threading.Event()
        self._mic_busy = threading.Lock()  # only one microphone user at a time

        self.title("MERLIN — Local AI Assistant")
        self.geometry("860x660")
        self.minsize(640, 480)
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_MS, self._poll_events)
        self._append("MERLIN online. Type a command, press 🎙 to talk, "
                      "or enable voice mode.", "info")

    def _build_widgets(self) -> None:
        # --- Header: status + voice switch ---
        header = ctk.CTkFrame(self, corner_radius=10)
        header.pack(fill="x", padx=14, pady=(14, 6))
        self.status_label = ctk.CTkLabel(
            header, text="Status: idle",
            font=ctk.CTkFont(size=13, weight="bold"))
        self.status_label.pack(side="left", padx=12, pady=8)
        self.voice_switch = ctk.CTkSwitch(
            header, text="Voice mode", variable=self.voice_mode,
            command=self._on_voice_toggle)
        self.voice_switch.pack(side="right", padx=12)

        # --- Chat bubbles ---
        self.chat = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.chat.pack(fill="both", expand=True, padx=10, pady=6)

        # --- Input row ---
        row = ctk.CTkFrame(self, corner_radius=10)
        row.pack(fill="x", padx=14, pady=(6, 14))
        self.entry = ctk.CTkEntry(
            row, placeholder_text="Speak or type your request...",
            font=ctk.CTkFont(size=14))
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=10)
        self.entry.bind("<Return>", lambda _e: self._on_send())
        self.send_btn = ctk.CTkButton(row, text="Send", width=80,
                                      command=self._on_send)
        self.send_btn.pack(side="left", padx=(0, 6))
        self.mic_btn = ctk.CTkButton(
            row, text="🎙", width=56, command=self._on_mic)
        self.mic_btn.pack(side="left", padx=(0, 10))

        # --- MERLIN's visible cursor overlay + floating widget + island bar ---
        if cursor_overlay.overlay_enabled():
            cursor_overlay.attach(cursor_overlay.CursorOverlay(self))
        self.widget = None
        self.island = None
        self._widget_mode = os.environ.get("MERLIN_WIDGET", "1") == "1"
        if self._widget_mode:
            from merlin.gui.island import DynamicIsland
            from merlin.gui.widget import MerlinWidget
            self.widget = MerlinWidget(self)
            self.island = DynamicIsland(self)
            self.withdraw()  # the dashboard stays hidden (context menu of the
            # widget opens it) - the desktop stays clean.
            if voice_tools.wake_word_available():
                # the floating wizard is always waiting for the wake word
                self.voice_mode.set(True)
                self._on_voice_toggle()

    # ---------------------------------------------------------------- actions
    def _on_send(self) -> None:
        query = self.entry.get().strip()
        if not query:
            return
        self.entry.delete(0, "end")
        self._append(query, "user")
        self.runtime.submit(query, voice_mode=False)

    def _on_mic(self) -> None:
        """Push-to-talk: record one utterance, transcribe it, submit the text."""
        if self._runtime_busy():
            self._append("MERLIN is still processing the previous request.",
                         "error")
            return
        if not self._mic_busy.acquire(blocking=False):
            self._append("The microphone is already in use "
                         "(voice mode is listening).", "info")
            return
        self._append("Recording - speak now; I stop listening when you pause.",
                     "info")
        threading.Thread(target=self._push_to_talk, daemon=True,
                         name="merlin-ptt").start()

    def _on_voice_toggle(self) -> None:
        """Hands-free mode: wake word -> record -> transcribe -> submit."""
        if not self.voice_mode.get():
            self._wake_stop.set()
            self._append("Voice mode disabled.", "info")
            return
        if not voice_tools.wake_word_available():
            self.voice_mode.set(False)
            self._append("Wake word unavailable (openwakeword/sounddevice missing). "
                         "Use the 🎙 button for push-to-talk.", "error")
            return
        self._wake_stop = threading.Event()  # fresh event: an old loop keeps its own
        words = ", ".join(get_config().wake_words)
        self._append(f"Voice mode enabled - say the wake word ({words}), "
                      "then your request.", "info")
        threading.Thread(target=self._wake_loop, args=(self._wake_stop,),
                         daemon=True, name="merlin-wake").start()

    def _on_close(self) -> None:
        if self._widget_mode and self.widget is not None:
            # the dashboard only hides; the floating widget keeps MERLIN alive.
            # Quit: right-click on the widget.
            self.withdraw()
            post_event("info", "Dashboard hidden. Double-click the widget to "
                                "reopen it; right-click the widget to quit.")
            return
        self._wake_stop.set()
        self.destroy()

    def quit_app(self) -> None:
        """Full shutdown (right-click on the floating widget)."""
        self._wake_stop.set()
        self.destroy()

    # ------------------------------------------------- widget bridge methods
    def toggle_dashboard(self) -> None:
        if self.state() == "withdrawn":
            self.deiconify()
        else:
            self.withdraw()

    def widget_submit(self, query: str) -> None:
        self._append(query, "user")
        # speak=True: text commands get spoken feedback (and the audible
        # yes/no confirmation for WhatsApp actions) even with the mic off.
        self.runtime.submit(query, voice_mode=False, speak=True)

    def push_to_talk_async(self) -> None:
        self._on_mic()

    # ------------------------------------------------------- voice (threads)
    def _runtime_busy(self) -> bool:
        return self.runtime._busy.locked()  # lock is held for a whole graph turn

    def _push_to_talk(self) -> None:
        try:
            self._capture_and_submit()
        finally:
            self._mic_busy.release()

    def _wake_loop(self, stop: threading.Event) -> None:
        """Wake word ONCE opens a voice session: after each answer MERLIN keeps
        listening directly (no re-arming) until nothing is said or the mode
        turns off - the conversation stays alive."""
        session = False
        with self._mic_busy:
            while not stop.is_set():
                if self._runtime_busy():
                    stop.wait(0.5)
                    continue
                if not session:
                    post_event("status", "waiting for wake word")
                    started = time.time()
                    word = voice_tools.listen_wake_word(timeout_seconds=30,
                                                        stop_event=stop)
                    if stop.is_set():
                        break
                    if word is None:
                        if time.time() - started < 1.0:  # mic/model error
                            stop.wait(2.0)
                        continue
                    session = True
                    post_event("info", f"Wake word detected ({word}) - session "
                                       "active. Keep talking; no need to say "
                                       "the wake word again.")
                else:
                    post_event("status", "listening (session)")
                ok = self._capture_and_submit()
                if stop.is_set():
                    break
                if ok and self._ends_session(self._last_heard):
                    session = False
                    post_event("info", "Voice session ended.")
                elif not ok:
                    session = False  # nothing said -> back to the wake word
                    post_event("info", "Voice session ended.")
        if not self._runtime_busy():
            post_event("status", "idle")

    _END_SESSION_RE = re.compile(
        r"\b(?:eso\s+es\s+todo|ya\s+est[aá]|gracias?\b(?:\s+merlin)?|that'?s\s+all|"
        r"thanks|nothing\s+else|eso\s+es\s+todo)\b", re.IGNORECASE)

    def _ends_session(self, text: str) -> bool:
        return bool(self._END_SESSION_RE.search(text or ""))

    def _capture_and_submit(self) -> bool:
        """Record one utterance, transcribe it and hand the text to the runtime.

        Runs in a background thread: it only talks to the UI through post_event.
        """
        post_event("status", "recording")
        audio_path = voice_tools.record_audio()
        if audio_path is None:
            post_event("error", "Could not record from the microphone.")
            post_event("status", "idle")
            return False

        post_event("status", "transcribing")
        result = voice_tools.transcribe_audio_stt(str(audio_path))
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass
        if not result.get("ok"):
            post_event("error", result.get("error") or "I could not understand the audio.")
            post_event("status", "idle")
            return False

        text = result["text"]
        self._last_heard = text
        post_event("user_input", text)  # shown as a user bubble
        # speak=True: MERLIN answers out loud (streaming TTS); the turn still
        # ends after TTS (voice_mode stays False - the GUI wake loop owns listening).
        return self.runtime.submit(text, voice_mode=False, speak=True)

    # ---------------------------------------------------------------- events
    def _poll_events(self) -> None:
        for event in drain_events():
            if event.type == "user_input":
                self._append(f"🎤 {event.data}", "user")
                self._widget_state("listening")
            elif event.type == "merlin_partial":
                self._render_partial(event.data)
                self._widget_state("speaking")
            elif event.type == "merlin_response":
                self._finalize_stream(event.data)
                self._widget_state("idle")
                if self.island and event.data:
                    show = getattr(self.island, "show_response", None)
                    if show:
                        show(event.data)
                self.status_label.configure(text="Status: idle")
            elif event.type == "status":
                self.status_label.configure(text=f"Status: {event.data}")
                state = _state_for(event.data)
                self._widget_state(state, detail=event.data)
                if state == "thinking":
                    self._show_typing()
            elif event.type == "error":
                self._append(f"⚠ {event.data}", "error")
            elif event.type == "info":
                self._append(event.data, "info")
        self._scroll_end()
        self.after(self.POLL_MS, self._poll_events)

    def _widget_state(self, state: str, detail: str = None) -> None:
        if self.widget:
            self.widget.set_state(state, detail)
        if self.island:
            self.island.set_state(state, detail)

    def _show_typing(self) -> None:
        """While the reasoning model thinks: an animated '...' MERLIN bubble."""
        if self._stream_bubble is None:
            self._stream_bubble = _Bubble(self.chat, "· · ·", who="merlin")

    def _render_partial(self, partial: str) -> None:
        """Token-by-token streaming fills the MERLIN bubble in real time."""
        if self._stream_bubble is None:
            self._stream_bubble = _Bubble(self.chat, partial, who="merlin")
        else:
            self._stream_bubble.set_text(partial)

    def _finalize_stream(self, response: str) -> None:
        """The streamed bubble becomes the final answer bubble."""
        if self._stream_bubble is not None:
            self._stream_bubble.set_text(response or "…")
            self._stream_bubble = None
        elif response:
            self._append(response, "merlin")

    def _append(self, text: str, tag: str) -> None:
        """Router: tag -> bubble kind (user / merlin / error / info)."""
        if tag == "user":
            _Bubble(self.chat, text, who="user", anchor="e")
        elif tag == "merlin":
            _Bubble(self.chat, text, who="merlin")
        elif tag == "error":
            _Bubble(self.chat, text, who="system", anchor="center",
                    fg=_ERROR_FG, font_size=13)
        else:  # info / system
            _Bubble(self.chat, text, who="system", anchor="center",
                    fg=_SYSTEM_FG, font_size=13)

    def _scroll_end(self) -> None:
        self.chat.update_idletasks()
        self.chat._parent_canvas.yview_moveto(1.0)


def run_gui(runtime) -> None:
    app = MerlinApp(runtime)
    app.mainloop()