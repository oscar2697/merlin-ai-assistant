# MERLIN project context

MERLIN is a local-first desktop assistant for Windows. It accepts typed or
spoken requests, routes them to focused agents, controls supported desktop
apps, and returns text or speech. This document describes the current source,
not a planned architecture.

## What works in the repository

- **Conversation and orchestration:** LangGraph state machine with a
  deterministic bilingual router and four execution paths: OS automation,
  OpenCode prompt injection, web/research, and direct conversation.
- **Local models:** `merlin/llm.py` talks directly to Ollama's `/api/chat` and
  `/api/tags` endpoints using `httpx`. `OLLAMA_HOST`, `MERLIN_MODEL`, and
  `MERLIN_FAST_MODEL` configure the server and model names. The default model
  is `qwen2.5-coder:3b`; installed models are checked without silently
  substituting a different one.
- **Voice:** endpointed microphone capture with `sounddevice`, wake-word
  detection with `openwakeword`, STT with `faster-whisper`, and Piper TTS.
  Spanish is the default language; English can be selected explicitly. During
  streamed answers, the floating results card follows the text as it is
  generated and spoken.
- **Desktop actions:** app launch, GUI interaction, screenshots, system
  resource status, file operations, media control, Word writing, WhatsApp file
  sharing/status updates, and staged LinkedIn/Facebook posts. Capabilities
  depend on the platform, browser session, and installed desktop apps.
- **Research:** ArXiv and web fetching/search use network access when invoked.
- **Persistence:** chat history and execution records are local JSON files
  under the configured MERLIN data/log paths.

## Runtime topology

```text
GUI / CLI / voice listener
            │
          router
    ┌───────┼────────┬───────────┐
  OS agent  OpenCode  Web/research  Direct response
    └───────┴────────┴───────────┘
                    │
               TTS responder
                    │
          voice listener or exit
```

The graph is assembled in `merlin/graph.py`. Self-improvement is not a graph
node: `MerlinRuntime` can start its prompt-maintenance routine in a background
thread when `MERLIN_SELF_IMPROVEMENT=1`. It is disabled by default.

## Configuration and platform notes

Configuration is loaded in `merlin/config.py` from environment variables and
an optional root `.env` file. See `.env.example`; keep real credentials and
personal data out of documentation and version control. The Windows path has
the broadest desktop automation support. `pywinauto` and `pywin32` are
Windows-only dependencies; macOS and Linux support varies by feature.

There is no visual-understanding model or camera/webcam perception pipeline
yet. MERLIN can capture screenshots, but screenshot capture alone does not
interpret what is on screen.
