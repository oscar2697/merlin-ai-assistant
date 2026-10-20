# MERLIN skills and tools

This is an inventory of implemented capabilities in `merlin/tools/` and the
agents that call them. Support may vary by operating system and installed app.

## OS and desktop (`merlin/tools/system_tools.py`)

- **System status:** CPU, memory, battery, and top processes via `psutil`.
- **Terminal:** execute a requested command with a configured timeout and a
  minimal destructive-command blacklist. The command is run through an
  explicitly selected platform shell with `shell=False` at the Python API.
- **Apps:** launch desktop apps by known executable, Windows Start Apps or
  shortcut, or the native platform launcher.
- **GUI interaction:** focus, type, and hotkeys through `pyautogui`; Windows
  UI Automation helpers use `pywinauto` when available.
- **Files:** list, move, copy, rename, and send deletions to the OS trash.
- **Screenshots:** capture the screen or a supported window to
  `~/merlin/screenshots/`. Captures can be attached to supported messaging or
  LinkedIn tasks. MERLIN does not currently interpret images visually.
- **Media:** playback, track search, and iTunes control where supported.
- **Web search launcher:** opens a browser search; result retrieval is handled
  separately by the web agent.

## Writing and communication

- **Word and desktop writing** (`merlin/tools/writing_tools.py`): generate text
  with the configured LLM, create or focus a document, insert text, and verify
  insertion where the platform exposes a suitable method.
- **WhatsApp** (`merlin/tools/whatsapp_tools.py`): send a message, share the
  latest screenshot or another local file, and start a voice/video call.
  File sharing always asks for confirmation, including when the message-send
  confirmation setting is disabled. Desktop interaction is Windows-oriented.
- **LinkedIn** (`merlin/tools/linkedin_tools.py`): prepare a post and optional
  image in the browser. Unsupported or missing attachments are rejected rather
  than silently omitted. Publishing is staged for human confirmation.
- **Social posts and stories** (`merlin/tools/social_tools.py`): stage Facebook
  feed posts and image stories, plus WhatsApp Web image status updates. The
  browser composer must be identifiable and open; MERLIN asks for confirmation
  before publishing. Instagram publishing is not implemented.

## Development and research

- **OpenCode** (`merlin/tools/opencode_tools.py`): write a prompt into the
  configured project context and optionally open the development app; read a
  specified status/output file.
- **ArXiv and web content** (`merlin/tools/web_tools.py`): search ArXiv, fetch
  a URL, or request DuckDuckGo instant-answer data. These actions need network
  access.
- **Web agent:** performs search and synthesizes the returned findings through
  the configured local model.

## Voice (`merlin/tools/voice_tools.py`)

- **Capture:** `sounddevice` records until speech ends, with a hard maximum
  duration and pre-roll for the beginning of the utterance.
- **Wake word:** `openwakeword` uses the configured pretrained `jarvis` model
  unless a custom model is supplied.
- **Speech recognition:** `faster-whisper` small model by default; Spanish is
  the default. `MERLIN_STT_LANGUAGE=auto` enables language comparison, and a
  spoken explicit language-change command can switch Spanish/English during a
  session.
- **Speech output:** Piper voices `es_ES-sharvard-medium` and
  `en_GB-alan-medium`. For streamed responses, finished sentences enter a
  playback queue while the model generates the rest; the UI receives the
  cumulative partial text for live display.

## Runtime and safety

The graph routes requests through `router`, then one of `os_agent`,
`opencode_agent`, `web_agent`, or `direct_response`, followed by
`tts_responder`. Prompt maintenance is optional startup work controlled by
`MERLIN_SELF_IMPROVEMENT=1`; it is not a graph node.

Destructive terminal commands are guarded by the configured blacklist. Some
external actions require confirmation. Voice recognition can be uncertain, so
unclear transcripts should be rejected before they reach an action agent.
