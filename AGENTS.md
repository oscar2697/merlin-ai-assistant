# MERLIN Multi-Agent LangGraph Architecture

## 1. Graph State Schema (GraphState)

The central memory state passed between nodes in the LangGraph execution flow:

- input_query: str (The user's spoken or text prompt)
- chat_history: List[dict] (Previous turns in conversation)
- active_agent: str (Currently active execution node)
- tool_calls: List[dict] (Tools invoked during the turn)
- tool_results: List[dict] (Outputs returned by tools)
- final_response: str (Synthesized final answer text)
- error_logs: List[str] (Captured exceptions or failures)
- system_prompt_override: str (Dynamic prompt adjusted by self-improvement)


## 2. Node Definitions

### A. Voice_Listener_Node
- Role: Captures speech input via openWakeWord and faster-whisper STT.
- Transition: Passes transcribed text as input_query to the Router_Node.

### B. Router_Node (Gemma 4 e4b - Primary Orchestrator)
- Role: Evaluates user intent from input_query and decides conditional routing. Uses a **hybrid strategy**:
  1. **Keyword matching first** (deterministic, zero tokens, zero latency) against a bilingual keyword map (English base + Spanish synonyms).
  2. **LLM fallback**: if no keyword matches, Gemma 4 e4b classifies the intent into one of the four routes with a structured single-label answer.
- Transitions:
  - If request is OS or system command -> OS_Agent_Node
  - If request is development or OpenCode dictation -> OpenCode_Agent_Node
  - If request is research or web search -> Web_Agent_Node
  - If general conversation -> Direct_Response_Node

### C. OS_Agent_Node
- Role: Executes system control skills (execute_terminal_command, control_system_resource, interact_gui_app).
- Transition: Sends result to TTS_Responder_Node.

### D. OpenCode_Agent_Node
- Role: Formulates coding prompts, opens OpenCode, and injects context (inject_opencode_prompt).
- Transition: Sends confirmation status to TTS_Responder_Node.

### E. Web_Agent_Node
- Role: Runs research queries via search_arxiv_papers or web scraping tools.
- Transition: Synthesizes results and routes to TTS_Responder_Node.

### F. Self_Improvement_Node (Startup Subgraph)
- Role: Executes automatically upon application boot.
  1. Inspects execution_logs.json for recent failures or slow tool invocations.
  2. Queries Gemma 4 e4b to generate optimized system prompt guidelines.
  3. Writes updates to system_prompt.txt before turning on the voice listener.
- Transition: Handover to Voice_Listener_Node.

### G. TTS_Responder_Node
- Role: Receives final_response text, plays audio via Piper TTS, and updates UI chat history.


## 3. Graph Routing Logic Specification

The routing function uses a **hybrid strategy** (deterministic first, LLM fallback):

### Step 1 - Keyword matching (bilingual: English base + Spanish synonyms)
- If query contains "opencode", "code", "código", "prompt", "desarrollo", or "develop" -> route to opencode_agent
- If query contains "terminal", "ram", "battery", "batería", "open", "abrir", "system", "sistema", "file", "archivo", "download", "descarga", "music", "itunes", or "whatsapp" -> route to os_agent
- If query contains "arxiv", "paper", "search", "buscar", "web", "investiga", or "research" -> route to web_agent
- Otherwise -> fall through to Step 2

### Step 2 - LLM fallback (Gemma 4 e4b via LM Studio)
- Gemma classifies the query with a structured single-label answer: `os_agent | opencode_agent | web_agent | direct_response`.
- If the LLM is unreachable, default to `direct_response` (graceful degradation).

## 4. LLM Connectivity

- Engine: **LM Studio** local server (OpenAI-compatible API, default `http://localhost:1234/v1`), model `google/gemma-4-e4b`, temperature 0.1.
- Connector: `langchain-openai` (`ChatOpenAI`) with API key from `.env`; model auto-detected via `GET /v1/models` at boot.
- Graceful fallback: if LM Studio is down, all LLM-dependent nodes degrade to deterministic/offline responses and log the failure to `error_logs`.

## 5. Deployment Notes

- **Autostart (Windows)**: Task Scheduler starts LM Studio headless (`lms load google/gemma-4-e4b`) and the MERLIN GUI at logon.
- **Installer**: PyInstaller bundle + Inno Setup (`.exe`); macOS (launchd + `.dmg`) is a future target.
- **Persistence (Milestone 1)**: local JSON stores under `logs/` (execution logs, chat history); Qdrant/Postgres migration later.