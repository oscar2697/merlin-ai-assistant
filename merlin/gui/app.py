"""CustomTkinter GUI: dark medieval-style chat with live node status + mic.

The graph runs in background threads (non-blocking UI per CONTEXT.md) and
communicates through the merlin.events queue.

Voice input lives here, outside the graph: the mic button is push-to-talk and
the "Voice mode" switch runs a hands-free wake-word loop in its own thread.
Both only touch the runtime once they have final text to submit, so the
runtime's busy lock is never held while waiting for the microphone.
"""
from __future__ import annotations

import logging
import threading
import time
import tkinter

import customtkinter as ctk

from merlin.config import get_config
from merlin.events import drain_events, post_event
from merlin.tools import voice_tools

logger = logging.getLogger("merlin.gui")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

_USER_TAG = "user"
_MERLIN_TAG = "merlin"
_ERROR_TAG = "error"


class MerlinApp(ctk.CTk):
    POLL_MS = 120

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.voice_mode = tkinter.BooleanVar(value=False)
        self._stream_start = None  # index where the streaming response begins
        self._wake_stop = threading.Event()
        self._mic_busy = threading.Lock()  # only one microphone user at a time

        self.title("MERLIN — Local AI Assistant")
        self.geometry("860x660")
        self.minsize(640, 480)
        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_MS, self._poll_events)
        self._append("MERLIN online. Type a command, press 🎙 to talk, "
                     "or enable voice mode.\n", "info")

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

        # --- Chat log ---
        self.chat = ctk.CTkTextbox(self, wrap="word",
                                   font=ctk.CTkFont(size=14))
        self.chat.pack(fill="both", expand=True, padx=14, pady=6)
        for tag, color in (("user", "#7fb4ff"), ("merlin", "#c9a86a"),
                           ("error", "#ff6b6b"), ("info", "#9aa0a6")):
            self.chat.tag_config(tag, foreground=color)

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

    # ---------------------------------------------------------------- actions
    def _on_send(self) -> None:
        query = self.entry.get().strip()
        if not query:
            return
        self.entry.delete(0, "end")
        self._append(f"You: {query}\n", _USER_TAG)
        self.runtime.submit(query, voice_mode=False)

    def _on_mic(self) -> None:
        """Push-to-talk: record one utterance, transcribe it, submit the text."""
        if self._runtime_busy():
            self._append("Error: MERLIN is still processing the previous request.\n",
                         _ERROR_TAG)
            return
        if not self._mic_busy.acquire(blocking=False):
            self._append("The microphone is already in use "
                         "(voice mode is listening).\n", "info")
            return
        seconds = get_config().record_seconds
        self._append(f"Recording for {seconds} s - speak now...\n", "info")
        threading.Thread(target=self._push_to_talk, daemon=True,
                         name="merlin-ptt").start()

    def _on_voice_toggle(self) -> None:
        """Hands-free mode: wake word -> record -> transcribe -> submit."""
        if not self.voice_mode.get():
            self._wake_stop.set()
            self._append("Voice mode disabled.\n", "info")
            return
        if not voice_tools.wake_word_available():
            self.voice_mode.set(False)
            self._append("Wake word unavailable (openwakeword/pyaudio missing). "
                         "Use the 🎙 button for push-to-talk.\n", _ERROR_TAG)
            return
        self._wake_stop = threading.Event()  # fresh event: an old loop keeps its own
        words = ", ".join(get_config().wake_words)
        self._append(f"Voice mode enabled - say the wake word ({words}), "
                     "then your request.\n", "info")
        threading.Thread(target=self._wake_loop, args=(self._wake_stop,),
                         daemon=True, name="merlin-wake").start()

    def _on_close(self) -> None:
        self._wake_stop.set()
        self.destroy()

    # ------------------------------------------------------- voice (threads)
    def _runtime_busy(self) -> bool:
        return self.runtime._busy.locked()  # lock is held for a whole graph turn

    def _push_to_talk(self) -> None:
        try:
            self._capture_and_submit()
        finally:
            self._mic_busy.release()

    def _wake_loop(self, stop: threading.Event) -> None:
        with self._mic_busy:
            while not stop.is_set():
                if self._runtime_busy():
                    stop.wait(0.5)
                    continue
                post_event("status", "waiting for wake word")
                started = time.time()
                word = voice_tools.listen_wake_word(timeout_seconds=30,
                                                    stop_event=stop)
                if stop.is_set():
                    break
                if word is None:
                    if time.time() - started < 1.0:  # mic/model error: no hot loop
                        stop.wait(2.0)
                    continue
                post_event("info", f"Wake word detected ({word}) - speak now...")
                self._capture_and_submit()
        if not self._runtime_busy():
            post_event("status", "idle")

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
        post_event("user_input", text)  # shown as "You (voice): ..."
        return self.runtime.submit(text, voice_mode=False)

    # ---------------------------------------------------------------- events
    def _poll_events(self) -> None:
        for event in drain_events():
            if event.type == "user_input":
                self._append(f"You (voice): {event.data}\n", _USER_TAG)
            elif event.type == "merlin_partial":
                self._render_partial(event.data)
            elif event.type == "merlin_response":
                self._finalize_stream(event.data)
                self.status_label.configure(text="Status: idle")
            elif event.type == "status":
                self.status_label.configure(text=f"Status: {event.data}")
            elif event.type == "error":
                self._append(f"Error: {event.data}\n", _ERROR_TAG)
            elif event.type == "info":
                self._append(f"{event.data}\n", "info")
        self.after(self.POLL_MS, self._poll_events)

    def _render_partial(self, partial: str) -> None:
        """Token-by-token streaming: rewrite the tail of the chat."""
        self.chat.configure(state="normal")
        if self._stream_start is None:
            self.chat.insert("end", "\nMERLIN: ", _MERLIN_TAG)
            self._stream_start = self.chat.index("end-1c")
        self.chat.delete(self._stream_start, "end-1c")
        self.chat.insert(self._stream_start, partial, _MERLIN_TAG)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _finalize_stream(self, response: str) -> None:
        """Replace the streamed partial with the final full text."""
        if self._stream_start is not None:
            self.chat.configure(state="normal")
            self.chat.delete(self._stream_start, "end-1c")
            self.chat.insert(self._stream_start, response or "", _MERLIN_TAG)
            self.chat.insert("end", "\n")
            self.chat.see("end")
            self.chat.configure(state="disabled")
            self._stream_start = None
        else:
            self._append(f"MERLIN: {response}\n", _MERLIN_TAG)

    def _append(self, text: str, tag: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")


def run_gui(runtime) -> None:
    app = MerlinApp(runtime)
    app.mainloop()