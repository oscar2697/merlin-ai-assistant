# MERLIN: contributor guide

Keep changes aligned with the code that exists today. This file is the current
repository guide; `README.md` is the user-facing setup and usage guide.

## Current architecture

- Python desktop assistant with a CustomTkinter GUI and optional CLI.
- LangGraph routes each turn through `voice_listener` (when input is empty),
  `router`, one of `os_agent`, `opencode_agent`, `web_agent`, or
  `direct_response`, and then `tts_responder`.
- The graph does **not** contain a self-improvement node. If
  `MERLIN_SELF_IMPROVEMENT=1`, `MerlinRuntime` runs the prompt-maintenance
  routine once in a background thread at startup.
- `merlin/llm.py` calls Ollama's HTTP API with `httpx`. The main and fast model
  names come from `MERLIN_MODEL` and `MERLIN_FAST_MODEL`.
- Voice input uses `sounddevice`, `openwakeword`, and `faster-whisper`; Piper
  provides speech output. Spanish is the default STT language; users can
  explicitly switch to English. The voice UI streams text to the results card
  while an answer is generated and spoken.
- Conversation history and execution logs are local JSON files. Web search and
  web automation use network access when requested.

## State and routing

`GraphState` is defined in `merlin/state.py`. It carries the query, history,
active agent, tool calls/results, final response, errors, language, and speech
flags. Keep state updates partial and avoid mutating shared state in place.

The router in `merlin/router.py` uses deterministic bilingual patterns for
common actions, then an optional LLM classifier only when
`MERLIN_ROUTER_LLM=1`. Unknown intent defaults to `direct_response`. Writing
verb patterns shared by the router and OS agent belong in
`merlin/text_intents.py`.

## Safety and implementation

- Validate tool names and arguments before dispatch. Unknown tools must fail
  closed without raising through the graph.
- Keep destructive or external actions behind their existing safeguards and
  confirmation flows. Never weaken them to make a test pass.
- Use `subprocess` with argument lists and `shell=False`; where a shell command
  string is an explicit feature, select the platform shell deliberately.
- Keep GUI work on its UI thread and long-running model/audio work off it. Send
  GUI updates through the event queues rather than touching Tk widgets from
  worker threads.
- Treat `.env`, logs, screenshots, chat history, and audio recordings as
  private local data. Do not print secrets or commit runtime data.
- Declare platform-specific dependencies with environment markers in
  `requirements.txt`; update docs when behavior or setup changes.
- Prefer focused changes over architecture rewrites. Do not add a model or a
  large dependency without evidence and a clear reason.

## Verification

Run targeted offline checks from the repository root with the active Python
environment, for example:

```powershell
python scripts/test_voice_word_routing.py
python scripts/test_voice_tts_queue.py
python scripts/test_writing_tools.py
```

Hardware-dependent checks (microphone, Word, WhatsApp, browser login) need a
real Windows session and should not be represented as passing from mocked
tests.
