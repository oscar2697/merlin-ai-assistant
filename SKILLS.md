# MERLIN Skills & Tools Specification (`skills.md`)

> **Permission model**: Near-total control. MERLIN may open apps, send messages (WhatsApp), download files, manage media libraries and run shell commands on voice request. Only **irreversible/destructive** actions are blocked by a minimal blacklist (see safety policies below).
> **Voice pipeline is bilingual**: faster-whisper auto-detects Spanish/English; Piper uses `es_ES-*` for Spanish and `en_GB-*` (British English) for English.

## 1. Local OS & System Control Skills

### `control_system_resource`
- **Description:** Queries real-time hardware status including CPU usage, RAM allocation, battery levels, and active system processes.
- **Input Schema:** `action: str` ("ram", "cpu", "battery", "processes")
- **Implementation:** `psutil` library.
- **Safety Policy:** Read-only monitoring. No system modification.

### `execute_terminal_command`
- **Description:** Executes subcommands, bash/powershell scripts, or system utilities in a controlled environment.
- **Input Schema:** `command: str`, `timeout_seconds: int = 30`
- **Implementation:** `subprocess.run(..., shell=True)`
- **Safety Policy:** Minimal blacklist of irreversible commands only (`rm -rf /`, `mkfs`, `dd if=`, raw drive formatting, `diskpart format`, registry HKLM deletion, firmware/BIOS writes). Everything else executes on voice command.

### `interact_gui_app`
- **Description:** Controls window focus, key simulation, typing, and hotkeys for desktop applications (e.g. "open WhatsApp and message mom").
- **Input Schema:** `app_name: str`, `action: str` ("focus", "type", "hotkey"), `payload: str`
- **Implementation:** `pyautogui` / `pynput` / native OS tools (`xdotool` or Windows API).

### `open_app`
- **Description:** Launches any installed application by name (resolves Start Menu / Applications folder / `start` shell command).
- **Input Schema:** `app_name: str`
- **Implementation:** `subprocess` (`start` on Windows, `open` on macOS) with fallback fuzzy match over Start Menu shortcuts.

### `manage_files`
- **Description:** Lists, moves, copies, renames, and deletes files/folders. Deletions always go to the OS trash (never permanent).
- **Input Schema:** `action: str` ("list", "move", "copy", "rename", "delete"), `source: str`, `destination: str = ""`
- **Implementation:** `pathlib` / `shutil` + `send2trash`.
- **Safety Policy:** Permanent deletion (bypassing trash) is blacklisted.

### `download_file`
- **Description:** Downloads a file from a URL to a user-chosen folder (e.g. a song from a page), reports progress and final path.
- **Input Schema:** `url: str`, `destination_folder: str = "Downloads"`
- **Implementation:** `httpx` streaming download.

### `control_media_player`
- **Description:** Manages media playback and libraries (e.g. iTunes/Apple Music): play, pause, skip, shuffle, or **play a specific song/artist/album** searched in the iTunes library.
- **Input Schema:** `action: str` ("play" | "pause" | "next" | "previous" | "stop" | "play_track" | "add_to_library"), `media_path: str = ""`, `shuffle: bool = False`, `track_name: str = ""` (song/artist/album, for play_track), `search_field: str = "all"` ("all" | "song" | "artist" | "album")
- **Implementation:** iTunes COM automation (`pywin32`, `LibraryPlaylist.Search`) with media-keys fallback (`pyautogui`). `play_track` auto-launches iTunes if closed and plays the best match; falls back to searching all fields if the specific field yields nothing.
- **Safety Policy:** Read-only search + playback; no library modification.

### `send_whatsapp_message`
- **Description:** Opens WhatsApp (desktop app or WhatsApp Web via Playwright), selects a contact, and sends a dictated message.
- **Input Schema:** `contact: str`, `message: str`
- **Implementation:** WhatsApp Desktop GUI automation with Playwright (WhatsApp Web) fallback.
- **Safety Policy:** Sending is allowed on voice command (near-total control model); message text is read back for confirmation when time permits.

---

## 2. OpenCode Orchestration Skills

### `inject_opencode_prompt`
- **Description:** Launches OpenCode/VSCode, sets up workspace focus, and injects structured development tasks or prompts into a dedicated markdown context file (`PROMPT_OPENCODE.md`) or CLI pipe.
- **Input Schema:** `project_path: str`, `prompt_content: str`
- **Implementation:** `subprocess.Popen` + local filesystem writer.

### `read_opencode_status`
- **Description:** Reads execution outputs, test results, or generated files from OpenCode sessions.
- **Input Schema:** `file_path: str`
- **Implementation:** File I/O reader with UTF-8 encoding.

---

## 3. Web & Scientific Research Skills

### `search_arxiv_papers`
- **Description:** Queries the ArXiv REST API for research papers on physics, machine learning, mathematics, or quantum computing.
- **Input Schema:** `query: str`, `max_results: int = 3`
- **Implementation:** `httpx` / `urllib` XML parsing for ArXiv API.

### `fetch_web_content`
- **Description:** Performs lightweight web search or fetches raw content from specific URLs. By default every web search **also opens the Google results in the browser** while MERLIN summarizes the findings (disable with `MERLIN_OPEN_BROWSER_ON_SEARCH=0`).
- **Input Schema:** `url_or_query: str`
- **Implementation:** DuckDuckGo search (`ddgs`, no API key) / Tavily / `httpx` web scraping.

---

## 4. Voice Pipeline Skills (Offline & Local)

### `listen_wake_word`
- **Description:** Runs a background listener waiting for the wake word "Merlin" or "Arthur" using minimal CPU resources.
- **Implementation:** `openWakeWord` stream listener.

### `transcribe_audio_stt`
- **Description:** Converts recorded speech buffer to text using an optimized local Whisper binary. **Bilingual**: automatic Spanish/English detection.
- **Implementation:** `faster-whisper` (Model: `small` or `medium`, `language=None` for auto-detect es/en).

### `synthesize_speech_tts`
- **Description:** Converts model response text into natural audio output. **Bilingual voices**: `es_ES-*` (Spanish) and `en_GB-*` (British English, per user requirement).
- **Implementation:** `Piper TTS` (offline C++/Python binding), voice selected by detected response language.