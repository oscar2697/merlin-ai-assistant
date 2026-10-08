# CONTEXT: Project MERLIN (Autonomous Local OS Agent)

## 1. Project Overview & Core Mission
**MERLIN** (formerly codenamed JARVIS) is a fully autonomous, voice-activated, medieval-themed local AI assistant and operating system controller. Designed to run completely on local consumer hardware without cloud dependencies, MERLIN seamlessly bridges:
- **System & Desktop Control**: Hands-free OS management, terminal execution, process monitoring, and dynamic GUI automation.
- **OpenCode Orchestration**: Direct injection and dictation of complex prompt sequences into developer tools and editors (e.g., OpenCode, VSCode).
- **Hybrid Intelligence**: Local deep reasoning combined with structured web search, ArXiv paper ingestion, and web automation.
- **Self-Improvement Loop**: On startup or user command, MERLIN reviews past execution logs, analyzes failures, refines its system prompts, and registers new skill routines.

---

## 2. Hardware Constraints & Resource Budget
The entire system architecture is calibrated strictly for the following host hardware:
- **Processor**: Intel® Core™ i7-1260P (12th Gen, 12 Cores / 16 Threads, up to 4.70 GHz).
- **RAM**: 16.0 GB LPDDR5 (Unified System & Graphics Memory).
- **GPU**: Integrated Intel® Iris® Xe Graphics (128 MB dedicated, shared system RAM).
- **Storage**: ~500+ GB available NVMe SSD.

### RAM Allocation Budget (16 GB Total)
| Component | Peak RAM Allocation | Operational Notes |
| :--- | :--- | :--- |
| **OS & Desktop Environment** | ~3.5 GB - 4.0 GB | Windows/Linux background baseline |
| **Gemma 4 e4b (LM Studio)** | ~6.3 GB | Q4_K_M quantization (~7.9B params total, ~4B active), served by LM Studio local server |
| **Docker (Qdrant + Postgres)** | ~1.2 GB | Vector storage & state history persistence |
| **Voice Pipeline (Whisper + Piper)** | ~0.8 GB | `faster-whisper` (medium/small model) + `Piper TTS` |
| **MERLIN Runtime & GUI** | ~0.5 GB | CustomTkinter + LangGraph state engine |
| **Buffer / Headroom** | **~3.0 GB** | Reserved for heavy OS tasks / browser tabs |

---

## 3. Technology Stack Specification

### Core Frameworks & LLM
- **Language**: Python 3.11+ (validated on 3.13)
- **Agent Framework**: **LangGraph** (StateGraph architecture for deterministic node routing, cyclical loops, and state persistence).
- **LLM Engine**: **LM Studio** local server exposing an OpenAI-compatible REST API (`http://localhost:1234/v1`), model `google/gemma-4-e4b` (temperature tuned to 0.1 for precise tool invocation). Connected via `langchain-openai` (`ChatOpenAI`); API key loaded from `.env`, never hard-coded. Model auto-detected at boot via `GET /v1/models`.

### Local Infrastructure & Databases
- **Docker Compose Orchestration**:
  - `qdrant/qdrant:v1.9.0` (Port 6333): Vector embeddings for long-term memory & paper RAG.
  - `postgres:16-alpine` (Port 5432): Chat session history, execution logs, and LangGraph state checkpoints.

### Voice Pipeline (Offline & Low-Latency)
- **Wake Word Detection**: `openWakeWord` (Configured for custom medieval wake-words: *"Merlin"*, *"Arthur"*).
- **Speech-to-Text (STT)**: `faster-whisper` (CPU-optimized CTranslate2 execution, **bilingual**: automatic Spanish/English language detection per utterance).
- **Text-to-Speech (TTS)**: `Piper TTS` (Fast offline neural speech synthesis with low latency). **Bilingual voices**: Spanish `es_ES-*` and British English `en_GB-*` (user requirement: English voice must be en_GB, not US).

### Desktop Control & Web Capabilities
- **UI Interface**: `CustomTkinter` (Dark-themed, medieval aesthetic desktop GUI). **Base language: English**; spoken responses follow the user's language.
- **OS Automation**: `psutil` (System health), `pyautogui` / `pynput` (GUI clicks, key presses, window focus), `subprocess` (Terminal & CLI commands).
- **Web Capabilities**: `httpx` (Async REST API calls, ArXiv API queries), `playwright` (Headless browser automation).

### Permission Model (Near-Total Control)
MERLIN operates with **near-total control** over the host laptop on voice command (open any app, send WhatsApp messages, download files, manage media libraries, execute shell commands). Only a minimal blacklist of **irreversible/destructive** actions is blocked (disk formatting, recursive system deletion, firmware/bIos writes, mass permanent deletion bypassing the trash).

---

## 4. Architectural State Flow (LangGraph Topology)

MERLIN operates as a directed state graph to handle complex user directives reliably:

```text
                        ┌────────────────────────┐
                        │   Voice/GUI Input      │
                        └───────────┬────────────┘
                                    │
                                    ▼
                        ┌────────────────────────┐
                        │   State Router Node    │
                        └─────┬──────┬──────┬────┘
                              │      │      │
            ┌─────────────────┘      │      └─────────────────┐
            ▼                        ▼                        ▼
┌───────────────────────┐ ┌────────────────────┐ ┌────────────────────────┐
│   OS / Desktop Agent  │ │  OpenCode Agent    │ │  Web / Research Agent  │
│ (subprocess, psutil)  │ │ (Prompt Injection) │ │  (ArXiv, Playwright)   │
└───────────┬───────────┘ └──────────┬─────────┘ └───────────┬────────────┘
            │                        │                       │
            └──────────────────┐     │     ┌─────────────────┘
                               ▼     ▼     ▼
                        ┌────────────────────────┐
                        │ Self-Reflection/Log    │
                        └───────────┬────────────┘
                                    │
                                    ▼
                        ┌────────────────────────┐
                        │   Piper TTS Output     │
                        └────────────────────────┘
```

---

## 5. Development Guidelines for OpenCode
1. **Single-Responsibility Tools**: Keep each `@tool` function isolated, type-hinted, and docstring-rich so Gemma 4 e4b can accurately choose the correct function.
2. **Non-Blocking UI**: Always execute heavy LLM invocations, voice listening loops, and system commands in dedicated `threading.Thread` background workers.
3. **Graceful Fallbacks**: If Docker or Qdrant are offline during boot, MERLIN must fall back to local JSON memory without crashing the entire system.

---

## 6. Deployment & Autostart (Windows-first, Mac future)
MERLIN lives permanently on the user's laptop and must be the first assistant available after boot:
- **Windows (primary target, i7-1260P / 16 GB)**:
  - Packaging: `PyInstaller` (single-folder bundle) + **Inno Setup** installer (`.exe`).
  - Autostart via **Task Scheduler** (at logon/unlock): first start LM Studio headless (`lms load google/gemma-4-e4b`, keep server on port 1234), then launch the MERLIN GUI.
- **macOS (future target, Apple Silicon assumed)**: launchd agent + `.dmg`/`.pkg` packaging. Directory structure and platform guards (`sys.platform`) are prepared now; no Mac build is produced in this milestone.
- **Persistence (Milestone 1)**: local JSON stores (`logs/execution_logs.json`, `logs/chat_history.json`) with a storage interface ready to migrate to Dockerized Qdrant + Postgres later.